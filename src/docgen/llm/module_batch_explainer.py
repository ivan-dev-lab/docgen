from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config, prompts
from .module_explainer import explain_module, load_explain_plan, metadata_output_path, unique_strings
from .run_history import record_generation_run

BATCH_MANIFEST_FILENAME = "llm-batch-run-manifest.json"
BATCH_MANIFEST_SCHEMA_VERSION = "1.0"
CACHE_KEY_VERSION = "stage_3d_cache_key_v1"
PROMPT_FINGERPRINT_VERSION = "stage_3d_module_explanation_prompt_v1"
OUTPUT_CONTRACT_VERSION = "stage_3c_module_explanation_markdown_v1"
BATCH_RESULT_STATUSES = {
    "dry_run_planned",
    "skipped_by_plan",
    "skipped_cached",
    "generated",
    "failed_preflight",
    "failed_generation",
}

ExplainModuleFunc = Callable[..., dict[str, Any]]


def explain_batch(
    plan_path: Path,
    output_path: Path,
    *,
    provider_name: str = "openrouter",
    model: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    no_cache: bool = False,
    only_modules: list[str] | None = None,
    limit: int | None = None,
    include_skip: bool = False,
    reasoning: bool | None = None,
    temperature: float = 0.2,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    continue_on_error: bool = True,
    explain_module_func: ExplainModuleFunc = explain_module,
) -> dict[str, Any]:
    if provider_name != "openrouter":
        raise ValueError(f"Unsupported provider: {provider_name}")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be zero or greater.")

    plan = load_explain_plan(plan_path)
    modules = [dict(module) for module in plan.get("modules", []) if isinstance(module, dict)]
    requested_names = unique_strings(only_modules or [])
    if requested_names:
        validate_requested_modules(modules, requested_names)
        considered = [module for module in modules if str(module.get("name") or "") in set(requested_names)]
    else:
        considered = modules

    resolved_model = model or str(plan.get("model_plan", {}).get("default_model") or config.DEFAULT_MODEL)
    reasoning_enabled = (
        reasoning
        if reasoning is not None
        else bool(plan.get("model_plan", {}).get("reasoning_enabled", config.DEFAULT_REASONING_ENABLED))
    )
    resolved_max_input_tokens = int(
        max_input_tokens or plan.get("budget_policy", {}).get("max_input_tokens_per_module") or 0
    )
    resolved_max_output_tokens = int(
        max_output_tokens or plan.get("budget_policy", {}).get("max_output_tokens_per_module") or 0
    )

    sorted_considered = sorted(considered, key=module_sort_key)
    skipped_by_plan = [
        module
        for module in sorted_considered
        if str(module.get("explain_mode") or "") == "skip" and not include_skip
    ]
    selected = [
        module
        for module in sorted_considered
        if not (str(module.get("explain_mode") or "") == "skip" and not include_skip)
    ]
    if limit is not None:
        selected = selected[:limit]

    results: list[dict[str, Any]] = []
    for module in skipped_by_plan:
        results.append(build_skipped_by_plan_result(output_path, module))

    for module in selected:
        result = run_batch_module(
            plan_path,
            output_path,
            module,
            provider_name=provider_name,
            model=resolved_model,
            dry_run=dry_run,
            force=force,
            no_cache=no_cache,
            include_skip=include_skip,
            reasoning=reasoning_enabled,
            temperature=temperature,
            max_input_tokens=resolved_max_input_tokens,
            max_output_tokens=resolved_max_output_tokens,
            explain_module_func=explain_module_func,
        )
        results.append(result)
        if result["status"].startswith("failed") and not continue_on_error:
            break

    results = sorted(results, key=lambda result: module_result_sort_key(result, modules))
    manifest = build_batch_manifest(
        plan_path=plan_path,
        output_path=output_path,
        provider_name=provider_name,
        model=resolved_model,
        dry_run=dry_run,
        reasoning_enabled=reasoning_enabled,
        temperature=temperature,
        selected=selected,
        considered=considered,
        results=results,
        force=force,
        no_cache=no_cache,
        include_skip=include_skip,
        limit=limit,
        continue_on_error=continue_on_error,
    )
    if not dry_run:
        manifest = record_generation_run(output_path, manifest)
    return manifest


def run_batch_module(
    plan_path: Path,
    output_root: Path,
    module: dict[str, Any],
    *,
    provider_name: str,
    model: str,
    dry_run: bool,
    force: bool,
    no_cache: bool,
    include_skip: bool,
    reasoning: bool,
    temperature: float,
    max_input_tokens: int,
    max_output_tokens: int,
    explain_module_func: ExplainModuleFunc,
) -> dict[str, Any]:
    module_name = str(module.get("name") or "")
    module_output_path = module_enhanced_output_path(output_root, module)
    metadata_path = metadata_output_path(module_output_path)
    start = time.perf_counter()
    base = base_result(module, module_output_path, metadata_path)

    try:
        preflight = explain_module_func(
            plan_path,
            module_name,
            module_output_path,
            provider_name=provider_name,
            model=model,
            dry_run=True,
            force=force,
            force_skip=include_skip,
            allow_over_budget=False,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning=reasoning,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return finish_result(
            {
                **base,
                "status": "failed_preflight",
                "cache_hit": False,
                "error": sanitize_error(str(exc)),
            },
            start,
        )

    context_fingerprint = str(preflight.get("context_fingerprint") or "")
    cache_key = build_cache_key(
        module_name=module_name,
        provider_name=provider_name,
        model=model,
        reasoning_enabled=reasoning,
        explain_mode=str(module.get("explain_mode") or ""),
        context_fingerprint=context_fingerprint,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    cache_hit = False if force or no_cache else is_cache_hit(module_output_path, metadata_path, cache_key)
    common = {
        **base,
        "cache_key": cache_key,
        "cache_hit": cache_hit,
        "context_fingerprint": context_fingerprint,
        "estimated_input_tokens": preflight.get("estimated_input_tokens"),
    }

    if cache_hit:
        return finish_result({**common, "status": "skipped_cached"}, start)
    if dry_run:
        return finish_result({**common, "status": "dry_run_planned"}, start)
    if preflight.get("would_require_allow_over_budget"):
        error = (
            "Estimated prompt tokens still exceed max_input_tokens after context reduction; "
            "batch generation does not override allow-over-budget."
        )
        return finish_result({**common, "status": "failed_preflight", "error": error}, start)
    if module_output_path.exists() and not (force or no_cache):
        error = "Output markdown exists but no valid matching cache metadata was found. Use --force or --no-cache."
        return finish_result({**common, "status": "failed_preflight", "error": error}, start)

    try:
        generated = explain_module_func(
            plan_path,
            module_name,
            module_output_path,
            provider_name=provider_name,
            model=model,
            dry_run=False,
            force=bool(force or no_cache),
            force_skip=include_skip,
            allow_over_budget=False,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning=reasoning,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return finish_result({**common, "status": "failed_generation", "error": sanitize_error(str(exc))}, start)

    usage = generated.get("usage") if isinstance(generated.get("usage"), dict) else None
    enrich_generation_metadata(metadata_path, cache_key=cache_key, batch_status="success")
    return finish_result(
        {
            **common,
            "status": "generated",
            "output_path": str(generated.get("output_path") or module_output_path.as_posix()),
            "metadata_path": str(generated.get("metadata_path") or metadata_path.as_posix()),
            "usage": usage,
        },
        start,
    )


def validate_requested_modules(modules: list[dict[str, Any]], requested_names: list[str]) -> None:
    available_names = sorted(unique_strings(str(module.get("name") or "") for module in modules if module.get("name")))
    missing = [name for name in requested_names if name not in set(available_names)]
    if not missing:
        return
    available = "\n".join(f"- {name}" for name in available_names)
    raise ValueError(
        "Requested module(s) were not found in explain-plan: "
        + ", ".join(missing)
        + f".\nAvailable modules:\n{available}"
    )


def build_skipped_by_plan_result(output_root: Path, module: dict[str, Any]) -> dict[str, Any]:
    module_output_path = module_enhanced_output_path(output_root, module)
    metadata_path = metadata_output_path(module_output_path)
    return {
        **base_result(module, module_output_path, metadata_path),
        "status": "skipped_by_plan",
        "cache_hit": False,
        "error": "explain_mode=skip; use --include-skip to include this module.",
        "duration_seconds": 0.0,
    }


def base_result(module: dict[str, Any], output_path: Path, metadata_path: Path) -> dict[str, Any]:
    return {
        "module": str(module.get("name") or ""),
        "status": "failed_preflight",
        "explain_mode": str(module.get("explain_mode") or ""),
        "priority": str(module.get("priority") or ""),
        "cache_key": None,
        "cache_hit": False,
        "context_fingerprint": None,
        "output_path": output_path.as_posix(),
        "metadata_path": metadata_path.as_posix(),
        "usage": None,
        "error": None,
        "duration_seconds": 0.0,
    }


def finish_result(result: dict[str, Any], start: float) -> dict[str, Any]:
    status = str(result.get("status") or "")
    if status not in BATCH_RESULT_STATUSES:
        result["status"] = "failed_generation"
        result["error"] = f"Unsupported batch result status: {status}"
    result["duration_seconds"] = round(max(0.0, time.perf_counter() - start), 3)
    return result


def module_enhanced_output_path(output_root: Path, module: dict[str, Any]) -> Path:
    module_doc_path = str(module.get("module_doc_path") or "").replace("\\", "/").strip("/")
    if module_doc_path:
        candidate = output_root / Path(module_doc_path)
    else:
        candidate = output_root / "modules" / f"module-{slugify_module_name(str(module.get('name') or 'unknown'))}.md"
    resolved_output_root = output_root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_output_root)
    except ValueError as exc:
        raise ValueError(f"Enhanced output path escapes output root: {candidate}") from exc
    return candidate


def slugify_module_name(value: str) -> str:
    text = value.strip().lower()
    safe = ["-" if char in {"\\", "/", " ", ".", "_"} else char for char in text]
    slug = "".join(char for char in safe if char.isalnum() or char == "-").strip("-")
    return slug or "unknown"


def is_cache_hit(output_path: Path, metadata_path: Path, cache_key: str) -> bool:
    if not output_path.exists() or not output_path.is_file():
        return False
    metadata = load_metadata(metadata_path)
    if metadata is None:
        return False
    if metadata.get("cache_key") != cache_key:
        return False
    if str(metadata.get("generation_status") or "").lower() not in {"success", "ok"}:
        return False
    if str(metadata.get("output_path") or output_path.as_posix()) != output_path.as_posix():
        return False
    return True


def load_metadata(metadata_path: Path) -> dict[str, Any] | None:
    if not metadata_path.exists() or not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def enrich_generation_metadata(metadata_path: Path, *, cache_key: str, batch_status: str) -> None:
    metadata = load_metadata(metadata_path)
    if metadata is None:
        return
    metadata["cache_key"] = cache_key
    metadata["cache_key_version"] = CACHE_KEY_VERSION
    metadata["generation_status"] = batch_status
    metadata["batch_generation"] = {
        "stage": "3D",
        "cache_key": cache_key,
        "status": batch_status,
        "updated_at": timestamp_now(),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_cache_key(
    *,
    module_name: str,
    provider_name: str,
    model: str,
    reasoning_enabled: bool,
    explain_mode: str,
    context_fingerprint: str,
    max_input_tokens: int,
    max_output_tokens: int,
    temperature: float,
) -> str:
    payload = {
        "cache_key_version": CACHE_KEY_VERSION,
        "module": module_name,
        "provider": provider_name,
        "model": model,
        "reasoning_enabled": bool(reasoning_enabled),
        "explain_mode": explain_mode,
        "context_fingerprint": context_fingerprint,
        "prompt_fingerprint": prompt_fingerprint(),
        "prompt_fingerprint_version": PROMPT_FINGERPRINT_VERSION,
        "output_contract_version": OUTPUT_CONTRACT_VERSION,
        "max_input_tokens": int(max_input_tokens),
        "max_output_tokens": int(max_output_tokens),
        "temperature": float(temperature),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prompt_fingerprint() -> str:
    payload = {
        "system": prompts.MODULE_EXPLANATION_SYSTEM_PROMPT,
        "user": prompts.MODULE_EXPLANATION_USER_TEMPLATE,
        "output_contract": prompts.MODULE_EXPLANATION_OUTPUT_CONTRACT,
        "version": PROMPT_FINGERPRINT_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_batch_manifest(
    *,
    plan_path: Path,
    output_path: Path,
    provider_name: str,
    model: str,
    dry_run: bool,
    reasoning_enabled: bool,
    temperature: float,
    selected: list[dict[str, Any]],
    considered: list[dict[str, Any]],
    results: list[dict[str, Any]],
    force: bool,
    no_cache: bool,
    include_skip: bool,
    limit: int | None,
    continue_on_error: bool,
) -> dict[str, Any]:
    counts = count_statuses(results)
    return {
        "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
        "generated_at": timestamp_now(),
        "plan_path": plan_path.as_posix(),
        "output_path": output_path.as_posix(),
        "provider": provider_name,
        "model": model,
        "dry_run": dry_run,
        "reasoning_enabled": bool(reasoning_enabled),
        "temperature": float(temperature),
        "force": bool(force),
        "no_cache": bool(no_cache),
        "include_skip": bool(include_skip),
        "limit": limit,
        "continue_on_error": bool(continue_on_error),
        "selected_modules": [str(module.get("name") or "") for module in selected],
        "total_modules_considered": len(considered),
        "total_modules_selected": len(selected),
        "generated_count": counts["generated"],
        "skipped_cached_count": counts["skipped_cached"],
        "skipped_by_plan_count": counts["skipped_by_plan"],
        "failed_count": counts["failed_preflight"] + counts["failed_generation"],
        "failed_preflight_count": counts["failed_preflight"],
        "failed_generation_count": counts["failed_generation"],
        "dry_run_planned_count": counts["dry_run_planned"],
        "estimated_generation_count": counts["dry_run_planned"] if dry_run else counts["generated"],
        "estimated_input_tokens_total": sum_estimated_input_tokens(results),
        "usage_totals": sum_usage_totals(results),
        "network_call": counts["generated"] > 0,
        "results": results,
        "warnings": [],
    }


def count_statuses(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in BATCH_RESULT_STATUSES}
    for result in results:
        status = str(result.get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def sum_usage_totals(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for result in results:
        if result.get("status") != "generated":
            continue
        usage = result.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in totals:
            try:
                totals[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
    return totals


def sum_estimated_input_tokens(results: list[dict[str, Any]]) -> int:
    total = 0
    for result in results:
        if result.get("status") not in {"dry_run_planned", "generated", "failed_generation"}:
            continue
        try:
            total += int(result.get("estimated_input_tokens") or 0)
        except (TypeError, ValueError):
            continue
    return total


def write_batch_manifest(output_path: Path, manifest: dict[str, Any]) -> Path:
    output_path.mkdir(parents=True, exist_ok=True)
    manifest_path = batch_manifest_path(output_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def batch_manifest_path(output_path: Path) -> Path:
    return output_path / BATCH_MANIFEST_FILENAME


def module_sort_key(module: dict[str, Any]) -> tuple[Any, ...]:
    return (
        priority_rank(str(module.get("priority") or "")),
        explain_mode_rank(str(module.get("explain_mode") or "")),
        str(module.get("module_page_role") or ""),
        str(module.get("name") or ""),
    )


def module_result_sort_key(result: dict[str, Any], modules: list[dict[str, Any]]) -> tuple[Any, ...]:
    module_name = str(result.get("module") or "")
    module = next((item for item in modules if str(item.get("name") or "") == module_name), {})
    return module_sort_key(module)


def priority_rank(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


def explain_mode_rank(value: str) -> int:
    return {"full": 0, "summary": 1, "skip": 2}.get(value, 3)


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_error(value: str) -> str:
    return value.replace("OPENROUTER_API=", "OPENROUTER_API_REDACTED=")
