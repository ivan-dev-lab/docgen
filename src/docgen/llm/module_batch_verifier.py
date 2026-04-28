from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config, prompts
from .module_batch_explainer import (
    load_metadata,
    module_enhanced_output_path,
    module_result_sort_key,
    module_sort_key,
    validate_requested_modules,
)
from .module_explainer import load_explain_plan, unique_strings
from .module_verifier import (
    DEFAULT_VERIFICATION_MAX_OUTPUT_TOKENS,
    verification_json_contract,
    verification_summary_path,
    verify_module,
)
from .run_history import record_verification_run

VERIFICATION_BATCH_MANIFEST_FILENAME = "llm-batch-verification-manifest.json"
VERIFICATION_BATCH_SCHEMA_VERSION = "1.0"
VERIFICATION_CACHE_KEY_VERSION = "stage_batch_verification_cache_key_v1"
VERIFICATION_PROMPT_FINGERPRINT_VERSION = "stage_3e_verification_prompt_v1"
VERIFICATION_OUTPUT_CONTRACT_VERSION = "stage_3e_verification_report_v1"
VERIFICATION_BATCH_STATUSES = {
    "dry_run_planned",
    "skipped_cached",
    "skipped_missing_enhanced",
    "verified_pass",
    "verified_warning",
    "verified_fail",
    "failed_preflight",
    "failed_verification",
}

VerifyModuleFunc = Callable[..., dict[str, Any]]


def verify_batch(
    plan_path: Path,
    enhanced_root: Path,
    output_root: Path,
    *,
    provider_name: str = "openrouter",
    model: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    no_cache: bool = False,
    only_modules: list[str] | None = None,
    limit: int | None = None,
    include_missing_enhanced: bool = False,
    verification_mode: str = "same_context",
    reasoning: bool | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = DEFAULT_VERIFICATION_MAX_OUTPUT_TOKENS,
    continue_on_error: bool = True,
    verify_module_func: VerifyModuleFunc = verify_module,
) -> dict[str, Any]:
    if provider_name != "openrouter":
        raise ValueError(f"Unsupported provider: {provider_name}")
    if verification_mode not in {"same_context", "fallback_plan"}:
        raise ValueError("verification-mode must be one of: same_context, fallback_plan.")
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
    reasoning_enabled = bool(reasoning) if reasoning is not None else False
    sorted_considered = sorted(considered, key=module_sort_key)
    candidates: list[dict[str, Any]] = []
    missing_enhanced: list[dict[str, Any]] = []
    for module in sorted_considered:
        enhanced_path = module_enhanced_output_path(enhanced_root, module)
        if enhanced_path.exists() and enhanced_path.is_file():
            candidates.append(module)
        else:
            missing_enhanced.append(module)
    selected = candidates[:limit] if limit is not None else candidates

    results: list[dict[str, Any]] = []
    for module in missing_enhanced:
        if include_missing_enhanced:
            results.append(build_missing_enhanced_result(enhanced_root, output_root, module, failed=True))
        else:
            results.append(build_missing_enhanced_result(enhanced_root, output_root, module, failed=False))

    for module in selected:
        result = run_verification_batch_module(
            plan_path,
            enhanced_root,
            output_root,
            module,
            provider_name=provider_name,
            model=resolved_model,
            dry_run=dry_run,
            force=force,
            no_cache=no_cache,
            verification_mode=verification_mode,
            reasoning=reasoning_enabled,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            verify_module_func=verify_module_func,
        )
        results.append(result)
        if result["status"] in {"failed_preflight", "failed_verification"} and not continue_on_error:
            break

    results = sorted(results, key=lambda result: module_result_sort_key(result, modules))
    manifest = build_verification_batch_manifest(
        plan_path=plan_path,
        enhanced_root=enhanced_root,
        output_root=output_root,
        provider_name=provider_name,
        model=resolved_model,
        dry_run=dry_run,
        verification_mode=verification_mode,
        reasoning_enabled=reasoning_enabled,
        temperature=temperature,
        selected=selected,
        considered=considered,
        results=results,
        force=force,
        no_cache=no_cache,
        include_missing_enhanced=include_missing_enhanced,
        limit=limit,
        continue_on_error=continue_on_error,
    )
    if not dry_run:
        manifest = record_verification_run(enhanced_root, output_root, manifest)
    return manifest


def run_verification_batch_module(
    plan_path: Path,
    enhanced_root: Path,
    output_root: Path,
    module: dict[str, Any],
    *,
    provider_name: str,
    model: str,
    dry_run: bool,
    force: bool,
    no_cache: bool,
    verification_mode: str,
    reasoning: bool,
    temperature: float,
    max_output_tokens: int,
    verify_module_func: VerifyModuleFunc,
) -> dict[str, Any]:
    module_name = str(module.get("name") or "")
    enhanced_path = module_enhanced_output_path(enhanced_root, module)
    verification_json_path = module_verification_output_path(output_root, module)
    verification_summary = verification_summary_path(verification_json_path)
    start = time.perf_counter()
    base = base_result(module, enhanced_path, verification_json_path, verification_summary, verification_mode)

    try:
        preflight = verify_module_func(
            plan_path,
            module_name,
            enhanced_path,
            verification_json_path,
            provider_name=provider_name,
            model=model,
            dry_run=True,
            force=force,
            reasoning=reasoning,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            verification_mode=verification_mode,
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
    context_source = str(preflight.get("context_source") or "")
    enhanced_fingerprint = file_fingerprint(enhanced_path)
    verification_cache_key = build_verification_cache_key(
        module_name=module_name,
        provider_name=provider_name,
        model=model,
        verification_mode=verification_mode,
        reasoning_enabled=reasoning,
        context_fingerprint=context_fingerprint,
        enhanced_markdown_fingerprint=enhanced_fingerprint,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    cache_hit = (
        False
        if force or no_cache
        else is_verification_cache_hit(verification_json_path, verification_summary, verification_cache_key)
    )
    common = {
        **base,
        "verification_cache_key": verification_cache_key,
        "cache_hit": cache_hit,
        "context_source": context_source,
        "context_fingerprint": context_fingerprint,
        "enhanced_markdown_fingerprint": enhanced_fingerprint,
        "estimated_input_tokens": preflight.get("estimated_input_tokens"),
    }

    if cache_hit:
        cached_report = load_metadata(verification_json_path) or {}
        return finish_result(
            {
                **common,
                "status": "skipped_cached",
                "verifier_status": cached_report.get("verifier_status"),
                "structured_output_valid": cached_report.get("structured_output_valid"),
                "verdict": cached_report.get("verdict"),
            },
            start,
        )
    if dry_run:
        return finish_result({**common, "status": "dry_run_planned"}, start)

    try:
        verified = verify_module_func(
            plan_path,
            module_name,
            enhanced_path,
            verification_json_path,
            provider_name=provider_name,
            model=model,
            dry_run=False,
            force=bool(force or no_cache),
            reasoning=reasoning,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            verification_mode=verification_mode,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return finish_result({**common, "status": "failed_verification", "error": sanitize_error(str(exc))}, start)

    usage = verified.get("usage") if isinstance(verified.get("usage"), dict) else None
    verifier_status = str(verified.get("verifier_status") or "")
    structured_output_valid = bool(verified.get("structured_output_valid"))
    verdict = normalize_verdict(verified.get("verdict"))
    status = verification_status_from_result(verifier_status, structured_output_valid, verdict)
    error = None
    if status == "failed_verification":
        error = (
            f"verifier_status={verifier_status or 'unknown'}, "
            f"structured_output_valid={structured_output_valid}, verdict={verdict}"
        )
    enrich_verification_report(
        verification_json_path,
        verification_cache_key=verification_cache_key,
        enhanced_markdown_fingerprint=enhanced_fingerprint,
    )
    return finish_result(
        {
            **common,
            "status": status,
            "verifier_status": verifier_status or None,
            "structured_output_valid": structured_output_valid,
            "verdict": verdict,
            "usage": usage,
            "error": error,
        },
        start,
    )


def build_missing_enhanced_result(
    enhanced_root: Path,
    output_root: Path,
    module: dict[str, Any],
    *,
    failed: bool,
) -> dict[str, Any]:
    enhanced_path = module_enhanced_output_path(enhanced_root, module)
    verification_json_path = module_verification_output_path(output_root, module)
    summary_path = verification_summary_path(verification_json_path)
    error = f"Enhanced markdown is missing: {enhanced_path.as_posix()}" if failed else None
    return {
        **base_result(module, enhanced_path, verification_json_path, summary_path, "same_context"),
        "status": "failed_preflight" if failed else "skipped_missing_enhanced",
        "cache_hit": False,
        "error": error,
        "duration_seconds": 0.0,
    }


def base_result(
    module: dict[str, Any],
    enhanced_path: Path,
    verification_json_path: Path,
    verification_summary_path_value: Path,
    verification_mode: str,
) -> dict[str, Any]:
    return {
        "module": str(module.get("name") or ""),
        "status": "failed_preflight",
        "verification_cache_key": None,
        "cache_hit": False,
        "verification_mode": verification_mode,
        "context_source": None,
        "context_fingerprint": None,
        "enhanced_markdown_path": enhanced_path.as_posix(),
        "verification_json_path": verification_json_path.as_posix(),
        "verification_summary_path": verification_summary_path_value.as_posix(),
        "verifier_status": None,
        "structured_output_valid": None,
        "verdict": None,
        "usage": None,
        "error": None,
        "duration_seconds": 0.0,
    }


def finish_result(result: dict[str, Any], start: float) -> dict[str, Any]:
    status = str(result.get("status") or "")
    if status not in VERIFICATION_BATCH_STATUSES:
        result["status"] = "failed_verification"
        result["error"] = f"Unsupported verification batch result status: {status}"
    result["duration_seconds"] = round(max(0.0, time.perf_counter() - start), 3)
    return result


def module_verification_output_path(output_root: Path, module: dict[str, Any]) -> Path:
    module_doc_path = str(module.get("module_doc_path") or "").replace("\\", "/").strip("/")
    if module_doc_path:
        stem = Path(module_doc_path).stem
    else:
        name = str(module.get("name") or "unknown").replace(":", "-").replace("/", "-").replace("\\", "-")
        stem = f"module-{name}"
    return output_root / f"{stem}.verification.json"


def is_verification_cache_hit(
    verification_json_path: Path,
    verification_summary: Path,
    verification_cache_key: str,
) -> bool:
    if not verification_json_path.exists() or not verification_json_path.is_file():
        return False
    if not verification_summary.exists() or not verification_summary.is_file():
        return False
    report = load_metadata(verification_json_path)
    if report is None:
        return False
    if report.get("verification_cache_key") != verification_cache_key:
        return False
    if report.get("verifier_status") != "ok":
        return False
    if report.get("structured_output_valid") is not True:
        return False
    if normalize_verdict(report.get("verdict")) is None:
        return False
    return True


def enrich_verification_report(
    verification_json_path: Path,
    *,
    verification_cache_key: str,
    enhanced_markdown_fingerprint: str,
) -> None:
    report = load_metadata(verification_json_path)
    if report is None:
        return
    report["verification_cache_key"] = verification_cache_key
    report["verification_cache_key_version"] = VERIFICATION_CACHE_KEY_VERSION
    report["enhanced_markdown_fingerprint"] = enhanced_markdown_fingerprint
    report["batch_verification"] = {
        "stage": "batch_verification",
        "verification_cache_key": verification_cache_key,
        "updated_at": timestamp_now(),
    }
    verification_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_verification_cache_key(
    *,
    module_name: str,
    provider_name: str,
    model: str,
    verification_mode: str,
    reasoning_enabled: bool,
    context_fingerprint: str,
    enhanced_markdown_fingerprint: str,
    max_output_tokens: int,
    temperature: float,
) -> str:
    payload = {
        "cache_key_version": VERIFICATION_CACHE_KEY_VERSION,
        "module": module_name,
        "provider": provider_name,
        "model": model,
        "verification_mode": verification_mode,
        "reasoning_enabled": bool(reasoning_enabled),
        "context_fingerprint": context_fingerprint,
        "enhanced_markdown_fingerprint": enhanced_markdown_fingerprint,
        "verification_prompt_fingerprint": verification_prompt_fingerprint(),
        "verification_prompt_fingerprint_version": VERIFICATION_PROMPT_FINGERPRINT_VERSION,
        "verification_output_contract_version": VERIFICATION_OUTPUT_CONTRACT_VERSION,
        "max_output_tokens": int(max_output_tokens),
        "temperature": float(temperature),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verification_prompt_fingerprint() -> str:
    payload = {
        "system": prompts.VERIFICATION_SYSTEM_PROMPT,
        "user": prompts.VERIFICATION_USER_TEMPLATE,
        "contract": verification_json_contract(),
        "version": VERIFICATION_PROMPT_FINGERPRINT_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verification_status_from_result(
    verifier_status: str,
    structured_output_valid: bool,
    verdict: str | None,
) -> str:
    if verifier_status != "ok" or not structured_output_valid:
        return "failed_verification"
    if verdict == "pass":
        return "verified_pass"
    if verdict == "warning":
        return "verified_warning"
    if verdict == "fail":
        return "verified_fail"
    return "failed_verification"


def normalize_verdict(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in {"pass", "warning", "fail"} else None


def build_verification_batch_manifest(
    *,
    plan_path: Path,
    enhanced_root: Path,
    output_root: Path,
    provider_name: str,
    model: str,
    dry_run: bool,
    verification_mode: str,
    reasoning_enabled: bool,
    temperature: float,
    selected: list[dict[str, Any]],
    considered: list[dict[str, Any]],
    results: list[dict[str, Any]],
    force: bool,
    no_cache: bool,
    include_missing_enhanced: bool,
    limit: int | None,
    continue_on_error: bool,
) -> dict[str, Any]:
    counts = count_statuses(results)
    return {
        "schema_version": VERIFICATION_BATCH_SCHEMA_VERSION,
        "generated_at": timestamp_now(),
        "plan_path": plan_path.as_posix(),
        "enhanced_root": enhanced_root.as_posix(),
        "output_root": output_root.as_posix(),
        "provider": provider_name,
        "model": model,
        "dry_run": dry_run,
        "verification_mode": verification_mode,
        "reasoning_enabled": bool(reasoning_enabled),
        "temperature": float(temperature),
        "force": bool(force),
        "no_cache": bool(no_cache),
        "include_missing_enhanced": bool(include_missing_enhanced),
        "limit": limit,
        "continue_on_error": bool(continue_on_error),
        "selected_modules": [str(module.get("name") or "") for module in selected],
        "total_modules_considered": len(considered),
        "total_modules_selected": len(selected),
        "verified_count": counts["verified_pass"] + counts["verified_warning"] + counts["verified_fail"],
        "warning_count": counts["verified_warning"],
        "failed_count": counts["failed_preflight"] + counts["failed_verification"],
        "skipped_cached_count": counts["skipped_cached"],
        "skipped_missing_enhanced_count": counts["skipped_missing_enhanced"],
        "failed_preflight_count": counts["failed_preflight"],
        "failed_verification_count": counts["failed_verification"],
        "verified_pass_count": counts["verified_pass"],
        "verified_warning_count": counts["verified_warning"],
        "verified_fail_count": counts["verified_fail"],
        "dry_run_planned_count": counts["dry_run_planned"],
        "estimated_input_tokens_total": sum_estimated_input_tokens(results),
        "usage_totals": sum_usage_totals(results),
        "network_call": (
            counts["verified_pass"] + counts["verified_warning"] + counts["verified_fail"] + counts["failed_verification"]
        )
        > 0,
        "results": results,
        "warnings": [],
    }


def count_statuses(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in VERIFICATION_BATCH_STATUSES}
    for result in results:
        status = str(result.get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def sum_usage_totals(results: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for result in results:
        if result.get("status") not in {"verified_pass", "verified_warning", "verified_fail", "failed_verification"}:
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
        if result.get("status") not in {
            "dry_run_planned",
            "verified_pass",
            "verified_warning",
            "verified_fail",
            "failed_verification",
        }:
            continue
        try:
            total += int(result.get("estimated_input_tokens") or 0)
        except (TypeError, ValueError):
            continue
    return total


def write_verification_batch_manifest(output_root: Path, manifest: dict[str, Any]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = verification_batch_manifest_path(output_root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def verification_batch_manifest_path(output_root: Path) -> Path:
    return output_root / VERIFICATION_BATCH_MANIFEST_FILENAME


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_error(value: str) -> str:
    return value.replace("OPENROUTER_API=", "OPENROUTER_API_REDACTED=")
