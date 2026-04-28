from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
CURRENT_STATE_FILENAME = "current-state.json"
MODULES_INDEX_FILENAME = "modules-index.json"
HISTORY_INDEX_FILENAME = "history-index.json"
HISTORY_RUNS_FILENAME = "history-runs.json"
UI_DATA_MANIFEST_FILENAME = "ui-data-manifest.json"


def build_ui_data(
    generated_root: Path,
    enhanced_root: Path,
    output_root: Path,
    *,
    strict: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    generated_root = generated_root.expanduser()
    enhanced_root = enhanced_root.expanduser()
    output_root = output_root.expanduser()
    warnings: list[str] = []
    sources = source_paths(generated_root, enhanced_root)
    payloads = load_sources(sources, strict=strict, warnings=warnings)
    explain_plan = payloads.get("explain_plan")
    if not isinstance(explain_plan, dict):
        raise ValueError(f"Missing required source manifest: {sources['explain_plan'].as_posix()}")

    generated_at = deterministic_generated_at(payloads)
    metadata_by_module = load_json_artifacts(
        enhanced_root / "llm-runs",
        "*.metadata.json",
        "generation metadata",
        warnings,
    )
    reports_by_module = load_json_artifacts(
        enhanced_root / "verification",
        "*.verification.json",
        "verification report",
        warnings,
    )

    generation_current = payloads.get("generation_current_manifest") or {}
    verification_current = payloads.get("verification_current_manifest") or {}
    generation_history = payloads.get("generation_history_index") or {}
    verification_history = payloads.get("verification_history_index") or {}
    ops_summary = payloads.get("ops_summary") or {}

    modules = build_modules_index(
        explain_plan=explain_plan,
        generated_root=generated_root,
        enhanced_root=enhanced_root,
        generated_at=generated_at,
        generation_current=generation_current if isinstance(generation_current, dict) else {},
        verification_current=verification_current if isinstance(verification_current, dict) else {},
        metadata_by_module=metadata_by_module,
        reports_by_module=reports_by_module,
        warnings=warnings,
    )
    history_index = build_history_index(
        generation_history if isinstance(generation_history, dict) else {},
        verification_history if isinstance(verification_history, dict) else {},
        verification_current if isinstance(verification_current, dict) else {},
        generated_at,
        warnings,
    )
    history_runs = build_history_runs(
        enhanced_root=enhanced_root,
        history_index=history_index,
        generation_current=generation_current if isinstance(generation_current, dict) else {},
        verification_current=verification_current if isinstance(verification_current, dict) else {},
        generated_at=generated_at,
        warnings=warnings,
    )
    current_state = build_current_state(
        sources=sources,
        generated_at=generated_at,
        generation_current=generation_current if isinstance(generation_current, dict) else {},
        verification_current=verification_current if isinstance(verification_current, dict) else {},
        ops_summary=ops_summary if isinstance(ops_summary, dict) else {},
        modules=modules["modules"],
        warnings=warnings,
    )
    ui_manifest = build_ui_data_manifest(
        output_root=output_root,
        sources=sources,
        generated_at=generated_at,
        warnings=warnings,
    )

    outputs = {
        "current_state": current_state,
        "modules_index": modules,
        "history_index": history_index,
        "history_runs": history_runs,
        "ui_data_manifest": ui_manifest,
    }
    if not dry_run:
        write_outputs(output_root, outputs)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "dry_run": dry_run,
        "output_root": normalize_path(output_root),
        "files": ui_manifest["files"],
        "module_count": len(modules["modules"]),
        "warnings": warnings,
        "network_call": False,
    }


def source_paths(generated_root: Path, enhanced_root: Path) -> dict[str, Path]:
    return {
        "explain_plan": generated_root / "explain-plan.json",
        "doc_manifest": generated_root / "doc-manifest.json",
        "generation_current_manifest": enhanced_root / "llm-batch-run-manifest.json",
        "verification_current_manifest": enhanced_root / "verification" / "llm-batch-verification-manifest.json",
        "generation_history_index": enhanced_root / "history" / "generation" / "index.json",
        "verification_history_index": enhanced_root / "history" / "verification" / "index.json",
        "ops_summary": enhanced_root / "ops-summary.json",
    }


def load_sources(paths: dict[str, Path], *, strict: bool, warnings: list[str]) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for name, path in paths.items():
        required = name == "explain_plan" or strict
        payload = load_json(path)
        if payload is None:
            message = f"Missing or invalid source manifest: {normalize_path(path)}"
            if required:
                raise ValueError(message)
            warnings.append(message)
            continue
        payloads[name] = payload
    return payloads


def build_modules_index(
    *,
    explain_plan: dict[str, Any],
    generated_root: Path,
    enhanced_root: Path,
    generated_at: str,
    generation_current: dict[str, Any],
    verification_current: dict[str, Any],
    metadata_by_module: dict[str, dict[str, Any]],
    reports_by_module: dict[str, dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    generation_results = results_by_module(generation_current)
    verification_results = results_by_module(verification_current)
    modules = [
        build_module_record(
            module,
            generated_root=generated_root,
            enhanced_root=enhanced_root,
            generation_current=generation_current,
            verification_current=verification_current,
            generation_result=generation_results.get(str(module.get("name") or "")),
            verification_result=verification_results.get(str(module.get("name") or "")),
            metadata=metadata_by_module.get(str(module.get("name") or "")),
            verification_report=reports_by_module.get(str(module.get("name") or "")),
            warnings=warnings,
        )
        for module in explain_plan.get("modules", [])
        if isinstance(module, dict)
    ]
    modules = sorted(modules, key=module_sort_key)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "modules": modules,
        "warnings": warnings,
    }


def build_module_record(
    module: dict[str, Any],
    *,
    generated_root: Path,
    enhanced_root: Path,
    generation_current: dict[str, Any],
    verification_current: dict[str, Any],
    generation_result: dict[str, Any] | None,
    verification_result: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    name = str(module.get("name") or "")
    module_doc_path = str(module.get("module_doc_path") or "")
    factual_path = generated_root / module_doc_path if module_doc_path else None
    factual_present = bool(factual_path and factual_path.exists() and module.get("module_doc_exists", True))
    if module_doc_path and module.get("module_doc_exists") and not factual_present:
        warnings.append(f"Factual module doc listed but missing for module {name}: {normalize_path(factual_path)}")

    enhanced_path = resolve_enhanced_path(enhanced_root, module_doc_path, generation_result, metadata)
    metadata_path = resolve_metadata_path(enhanced_root, generation_result, metadata)
    enhanced_present = enhanced_path.exists() if enhanced_path else False
    verification_json_path = resolve_verification_json_path(enhanced_root, module_doc_path, verification_result, verification_report)
    verification_summary_path = resolve_verification_summary_path(enhanced_root, verification_json_path, verification_result)
    verification_present = verification_report is not None and verification_json_path.exists()

    verdict = normalize_verdict(value_from(verification_report, verification_result, "verdict"))
    verifier_status = value_from(verification_report, verification_result, "verifier_status")
    structured_output_valid = value_from(verification_report, verification_result, "structured_output_valid")
    return {
        "name": name,
        "type": str(module.get("type") or ""),
        "module_page_role": str(module.get("module_page_role") or ""),
        "explain_mode": str(module.get("explain_mode") or ""),
        "priority": str(module.get("priority") or ""),
        "factual": {
            "present": factual_present,
            "module_doc_path": normalize_path(factual_path) if factual_path else None,
            "source_files": normalize_string_paths(module.get("source_files")),
            "file_doc_paths": normalize_file_doc_paths(generated_root, module.get("file_doc_paths")),
        },
        "enhanced": {
            "present": enhanced_present,
            "markdown_path": normalize_path(enhanced_path) if enhanced_path else None,
            "metadata_path": normalize_path(metadata_path) if metadata_path else None,
            "generation_status": generation_status(enhanced_present, generation_result, metadata),
            "batch_status": generation_result.get("status") if generation_result else None,
            "generation_run_id": generation_current.get("run_id") if generation_result else None,
            "context_fingerprint": value_from(generation_result, metadata, "context_fingerprint"),
        },
        "verification": {
            "present": verification_present,
            "json_path": normalize_path(verification_json_path) if verification_json_path else None,
            "summary_path": normalize_path(verification_summary_path) if verification_summary_path else None,
            "verification_status": verification_status(verification_present, verification_result, verification_report),
            "batch_status": verification_result.get("status") if verification_result else None,
            "verifier_status": verifier_status,
            "structured_output_valid": structured_output_valid,
            "verdict": verdict,
            "verification_run_id": verification_current.get("run_id") if verification_result else None,
            "unsupported_claims_count": count_list(verification_report, "unsupported_claims"),
            "weak_claims_count": count_list(verification_report, "weak_claims"),
            "missing_uncertainty_count": count_list(verification_report, "missing_uncertainty"),
            "missing_factual_support_count": count_list(verification_report, "missing_factual_support"),
        },
        "links": {
            "generation_history_manifest_path": generation_current.get("history_manifest_path") if generation_result else None,
            "verification_history_manifest_path": verification_current.get("history_manifest_path") if verification_result else None,
        },
    }


def build_current_state(
    *,
    sources: dict[str, Path],
    generated_at: str,
    generation_current: dict[str, Any],
    verification_current: dict[str, Any],
    ops_summary: dict[str, Any],
    modules: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "sources": {name: normalize_path(path) for name, path in sources.items() if name in {
            "explain_plan",
            "doc_manifest",
            "generation_current_manifest",
            "verification_current_manifest",
            "ops_summary",
        }},
        "latest_generation_run": latest_generation_run(generation_current),
        "latest_verification_run": latest_verification_run(verification_current),
        "ops_summary": {
            "generation_history_count": int_value(ops_summary.get("generation_history_count")),
            "verification_history_count": int_value(ops_summary.get("verification_history_count")),
            "latest_generation_cache_hit_rate": ops_summary.get("latest_generation_cache_hit_rate"),
            "latest_verification_cache_hit_rate": ops_summary.get("latest_verification_cache_hit_rate"),
            "warnings": list_value(ops_summary.get("warnings")),
        },
        "module_counts": module_counts(modules),
        "warnings": warnings,
    }


def build_history_index(
    generation_history: dict[str, Any],
    verification_history: dict[str, Any],
    verification_current: dict[str, Any],
    generated_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generation_runs": normalize_generation_runs(generation_history.get("runs")),
        "verification_runs": normalize_verification_runs(verification_history.get("runs"), verification_current),
        "warnings": warnings,
    }


def build_ui_data_manifest(
    *,
    output_root: Path,
    sources: dict[str, Path],
    generated_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "output_root": normalize_path(output_root),
        "files": {
            "current_state": normalize_path(output_root / CURRENT_STATE_FILENAME),
            "modules_index": normalize_path(output_root / MODULES_INDEX_FILENAME),
            "history_index": normalize_path(output_root / HISTORY_INDEX_FILENAME),
            "history_runs": normalize_path(output_root / HISTORY_RUNS_FILENAME),
        },
        "sources": {name: normalize_path(path) for name, path in sources.items()},
        "warnings": warnings,
    }


def build_history_runs(
    *,
    enhanced_root: Path,
    history_index: dict[str, Any],
    generation_current: dict[str, Any],
    verification_current: dict[str, Any],
    generated_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    generation_runs = [
        build_history_run_detail("generation", run, enhanced_root, generation_current, warnings)
        for run in history_index.get("generation_runs", [])
        if isinstance(run, dict)
    ]
    verification_runs = [
        build_history_run_detail("verification", run, enhanced_root, verification_current, warnings)
        for run in history_index.get("verification_runs", [])
        if isinstance(run, dict)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generation_runs": generation_runs,
        "verification_runs": verification_runs,
        "warnings": warnings,
    }


def build_history_run_detail(
    kind: str,
    index_run: dict[str, Any],
    enhanced_root: Path,
    current_manifest: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    manifest_path = index_run.get("manifest_path")
    manifest = load_history_manifest(manifest_path, enhanced_root, kind, index_run.get("run_id"), warnings)
    selected_modules = list_value(manifest.get("selected_modules")) or list_value(index_run.get("selected_modules"))
    results = normalize_history_results(kind, manifest.get("results"))
    run = {
        "run_id": index_run.get("run_id"),
        "kind": kind,
        "generated_at": manifest.get("generated_at") or index_run.get("generated_at"),
        "manifest_path": normalize_optional_path(manifest_path),
        "dry_run": bool(manifest.get("dry_run", index_run.get("dry_run"))),
        "provider": manifest.get("provider") or index_run.get("provider"),
        "model": manifest.get("model") or index_run.get("model"),
        "selected_modules": selected_modules,
        "selected_modules_count": len(selected_modules),
        "usage_totals": usage_totals(manifest if manifest else index_run),
        "estimated_input_tokens_total": int_value(manifest.get("estimated_input_tokens_total")),
        "latest_live_run": bool(index_run.get("run_id") and index_run.get("run_id") == current_manifest.get("run_id")),
        "cache_hit_rate": history_cache_hit_rate(manifest, index_run, len(results)),
        "result_status_counts": count_result_statuses(results),
        "results": results,
        "warnings": list_value(manifest.get("warnings")),
    }
    if kind == "generation":
        run.update(
            {
                "generated_count": int_value(manifest.get("generated_count", index_run.get("generated_count"))),
                "skipped_cached_count": int_value(
                    manifest.get("skipped_cached_count", index_run.get("skipped_cached_count"))
                ),
                "skipped_by_plan_count": int_value(
                    manifest.get("skipped_by_plan_count", index_run.get("skipped_by_plan_count"))
                ),
                "failed_count": int_value(manifest.get("failed_count", index_run.get("failed_count"))),
            }
        )
    else:
        run.update(
            {
                "verification_mode": manifest.get("verification_mode") or index_run.get("verification_mode"),
                "verified_count": int_value(manifest.get("verified_count", index_run.get("verified_count"))),
                "warning_count": int_value(manifest.get("warning_count", index_run.get("warning_count"))),
                "failed_count": int_value(manifest.get("failed_count", index_run.get("failed_count"))),
                "skipped_cached_count": int_value(
                    manifest.get("skipped_cached_count", index_run.get("skipped_cached_count"))
                ),
                "skipped_missing_enhanced_count": int_value(
                    manifest.get("skipped_missing_enhanced_count", index_run.get("skipped_missing_enhanced_count"))
                ),
            }
        )
    return run


def load_history_manifest(
    manifest_path: Any,
    enhanced_root: Path,
    kind: str,
    run_id: Any,
    warnings: list[str],
) -> dict[str, Any]:
    if not manifest_path:
        warnings.append(f"History {kind} run has no manifest_path: {run_id}")
        return {}
    path = resolve_recorded_path(manifest_path, enhanced_root)
    payload = load_json(path)
    if payload is None:
        warnings.append(f"Missing or invalid history manifest for {kind} run {run_id}: {normalize_path(path)}")
        return {}
    return payload


def normalize_history_results(kind: str, results: Any) -> list[dict[str, Any]]:
    normalized = []
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict):
            continue
        if kind == "generation":
            normalized.append(normalize_generation_result(result))
        else:
            normalized.append(normalize_verification_result(result))
    return sorted(normalized, key=lambda item: str(item.get("module") or ""))


def normalize_generation_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "module": result.get("module"),
        "status": result.get("status"),
        "priority": result.get("priority"),
        "explain_mode": result.get("explain_mode"),
        "cache_hit": bool(result.get("cache_hit")),
        "usage": normalize_usage(result.get("usage")),
        "duration_seconds": number_or_none(result.get("duration_seconds")),
        "error": result.get("error"),
        "output_path": normalize_optional_path(result.get("output_path")),
        "metadata_path": normalize_optional_path(result.get("metadata_path")),
        "context_fingerprint": result.get("context_fingerprint"),
    }


def normalize_verification_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "module": result.get("module"),
        "status": result.get("status"),
        "verifier_status": result.get("verifier_status"),
        "structured_output_valid": result.get("structured_output_valid"),
        "verdict": result.get("verdict"),
        "cache_hit": bool(result.get("cache_hit")),
        "usage": normalize_usage(result.get("usage")),
        "duration_seconds": number_or_none(result.get("duration_seconds")),
        "error": result.get("error"),
        "enhanced_markdown_path": normalize_optional_path(result.get("enhanced_markdown_path")),
        "verification_json_path": normalize_optional_path(result.get("verification_json_path")),
        "verification_summary_path": normalize_optional_path(result.get("verification_summary_path")),
        "context_fingerprint": result.get("context_fingerprint"),
    }


def normalize_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        "prompt_tokens": int_value(value.get("prompt_tokens")),
        "completion_tokens": int_value(value.get("completion_tokens")),
        "total_tokens": int_value(value.get("total_tokens")),
    }


def number_or_none(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def count_result_statuses(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def history_cache_hit_rate(manifest: dict[str, Any], index_run: dict[str, Any], result_count: int) -> float | None:
    selected_count = int_value(manifest.get("total_modules_selected", index_run.get("selected_modules_count")))
    if selected_count <= 0:
        selected_count = len(list_value(manifest.get("selected_modules"))) or len(list_value(index_run.get("selected_modules")))
    if selected_count <= 0:
        selected_count = result_count
    if selected_count <= 0:
        return None
    return round(int_value(manifest.get("skipped_cached_count", index_run.get("skipped_cached_count"))) / selected_count, 4)


def latest_generation_run(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest.get("run_id"),
        "history_manifest_path": normalize_optional_path(manifest.get("history_manifest_path")),
        "generated_count": int_value(manifest.get("generated_count")),
        "skipped_cached_count": int_value(manifest.get("skipped_cached_count")),
        "skipped_by_plan_count": int_value(manifest.get("skipped_by_plan_count")),
        "failed_count": int_value(manifest.get("failed_count")),
        "usage_totals": usage_totals(manifest),
    }


def latest_verification_run(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest.get("run_id"),
        "history_manifest_path": normalize_optional_path(manifest.get("history_manifest_path")),
        "verified_count": int_value(manifest.get("verified_count")),
        "warning_count": int_value(manifest.get("warning_count")),
        "failed_count": int_value(manifest.get("failed_count")),
        "skipped_cached_count": int_value(manifest.get("skipped_cached_count")),
        "skipped_missing_enhanced_count": int_value(manifest.get("skipped_missing_enhanced_count")),
        "usage_totals": usage_totals(manifest),
    }


def normalize_generation_runs(runs: Any) -> list[dict[str, Any]]:
    normalized = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        normalized.append(
            {
                "run_id": run.get("run_id"),
                "generated_at": run.get("generated_at"),
                "manifest_path": normalize_optional_path(run.get("manifest_path")),
                "dry_run": bool(run.get("dry_run")),
                "provider": run.get("provider"),
                "model": run.get("model"),
                "selected_modules": list_value(run.get("selected_modules")),
                "generated_count": int_value(run.get("generated_count")),
                "skipped_cached_count": int_value(run.get("skipped_cached_count")),
                "skipped_by_plan_count": int_value(run.get("skipped_by_plan_count")),
                "failed_count": int_value(run.get("failed_count")),
                "usage_totals": usage_totals(run),
            }
        )
    return sorted(normalized, key=lambda item: str(item.get("generated_at") or ""), reverse=True)


def normalize_verification_runs(runs: Any, verification_current: dict[str, Any]) -> list[dict[str, Any]]:
    current_run_id = verification_current.get("run_id")
    normalized = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        verification_mode = run.get("verification_mode")
        if run.get("run_id") == current_run_id:
            verification_mode = verification_current.get("verification_mode")
        normalized.append(
            {
                "run_id": run.get("run_id"),
                "generated_at": run.get("generated_at"),
                "manifest_path": normalize_optional_path(run.get("manifest_path")),
                "dry_run": bool(run.get("dry_run")),
                "provider": run.get("provider"),
                "model": run.get("model"),
                "verification_mode": verification_mode,
                "selected_modules": list_value(run.get("selected_modules")),
                "verified_count": int_value(run.get("verified_count")),
                "warning_count": int_value(run.get("warning_count")),
                "failed_count": int_value(run.get("failed_count")),
                "skipped_cached_count": int_value(run.get("skipped_cached_count")),
                "skipped_missing_enhanced_count": int_value(run.get("skipped_missing_enhanced_count")),
                "usage_totals": usage_totals(run),
            }
        )
    return sorted(normalized, key=lambda item: str(item.get("generated_at") or ""), reverse=True)


def module_counts(modules: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total_modules": len(modules),
        "with_factual": 0,
        "with_enhanced": 0,
        "with_verification": 0,
        "verification_pass": 0,
        "verification_warning": 0,
        "verification_fail": 0,
        "missing_enhanced": 0,
        "missing_verification": 0,
    }
    for module in modules:
        factual = module.get("factual") if isinstance(module.get("factual"), dict) else {}
        enhanced = module.get("enhanced") if isinstance(module.get("enhanced"), dict) else {}
        verification = module.get("verification") if isinstance(module.get("verification"), dict) else {}
        if factual.get("present"):
            counts["with_factual"] += 1
        if enhanced.get("present"):
            counts["with_enhanced"] += 1
        else:
            counts["missing_enhanced"] += 1
        if verification.get("present"):
            counts["with_verification"] += 1
        else:
            counts["missing_verification"] += 1
        verdict = verification.get("verdict")
        if verdict == "pass":
            counts["verification_pass"] += 1
        elif verdict == "warning":
            counts["verification_warning"] += 1
        elif verdict == "fail":
            counts["verification_fail"] += 1
    return counts


def results_by_module(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = manifest.get("results") if isinstance(manifest, dict) else None
    by_module = {}
    for result in results if isinstance(results, list) else []:
        if isinstance(result, dict) and result.get("module"):
            by_module[str(result.get("module"))] = result
    return by_module


def load_json_artifacts(root: Path, pattern: str, label: str, warnings: list[str]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    if not root.exists():
        warnings.append(f"Missing optional {label} directory: {normalize_path(root)}")
        return artifacts
    for path in sorted(root.glob(pattern)):
        payload = load_json(path)
        if payload is None:
            warnings.append(f"Invalid {label} JSON skipped: {normalize_path(path)}")
            continue
        module_name = str(payload.get("module") or "")
        if not module_name:
            warnings.append(f"{label} JSON has no module field: {normalize_path(path)}")
            continue
        payload = dict(payload)
        payload["_artifact_path"] = path
        artifacts[module_name] = payload
    return artifacts


def resolve_enhanced_path(
    enhanced_root: Path,
    module_doc_path: str,
    generation_result: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> Path | None:
    path = value_from(generation_result, metadata, "output_path")
    if path:
        return resolve_recorded_path(path, enhanced_root)
    if module_doc_path:
        return enhanced_root / module_doc_path
    return None


def resolve_metadata_path(
    enhanced_root: Path,
    generation_result: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> Path | None:
    path = value_from(generation_result, metadata, "metadata_path")
    if path:
        return resolve_recorded_path(path, enhanced_root)
    artifact_path = metadata.get("_artifact_path") if isinstance(metadata, dict) else None
    return artifact_path if isinstance(artifact_path, Path) else None


def resolve_verification_json_path(
    enhanced_root: Path,
    module_doc_path: str,
    verification_result: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
) -> Path | None:
    path = value_from(verification_result, verification_report, "verification_json_path")
    if path:
        return resolve_recorded_path(path, enhanced_root)
    artifact_path = verification_report.get("_artifact_path") if isinstance(verification_report, dict) else None
    if isinstance(artifact_path, Path):
        return artifact_path
    if module_doc_path:
        return enhanced_root / "verification" / f"{Path(module_doc_path).stem}.verification.json"
    return None


def resolve_verification_summary_path(
    enhanced_root: Path,
    verification_json_path: Path | None,
    verification_result: dict[str, Any] | None,
) -> Path | None:
    path = verification_result.get("verification_summary_path") if isinstance(verification_result, dict) else None
    if path:
        return resolve_recorded_path(path, enhanced_root)
    return verification_json_path.with_suffix(".md") if verification_json_path else None


def resolve_recorded_path(value: Any, artifact_root: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    bases = [Path.cwd()]
    if artifact_root.name in {"enhanced", "generated"} and artifact_root.parent.name == "docs":
        bases.insert(0, artifact_root.parent.parent)
    for base in bases:
        candidate = base / path
        if candidate.exists():
            return candidate
    return bases[0] / path


def generation_status(
    enhanced_present: bool,
    generation_result: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> str:
    status = str(generation_result.get("status") or "") if generation_result else ""
    if status in {"generated", "skipped_cached"}:
        return status
    if not enhanced_present:
        return "missing"
    metadata_status = str(metadata.get("generation_status") or "").lower() if isinstance(metadata, dict) else ""
    if metadata_status in {"success", "ok", "generated"}:
        return "generated"
    return "unknown"


def verification_status(
    verification_present: bool,
    verification_result: dict[str, Any] | None,
    verification_report: dict[str, Any] | None,
) -> str:
    if not verification_present:
        return "missing"
    status = str(verification_result.get("status") or "") if verification_result else ""
    if status in {"verified_pass", "verified_warning", "verified_fail"}:
        return status
    verifier_status = value_from(verification_report, verification_result, "verifier_status")
    structured_output_valid = value_from(verification_report, verification_result, "structured_output_valid")
    verdict = normalize_verdict(value_from(verification_report, verification_result, "verdict"))
    if verifier_status == "ok" and structured_output_valid is True and verdict in {"pass", "warning", "fail"}:
        return f"verified_{verdict}"
    return "unknown"


def normalize_file_doc_paths(generated_root: Path, value: Any) -> list[dict[str, Any]]:
    normalized = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            entry = dict(item)
            if entry.get("doc_path"):
                entry["doc_path"] = normalize_path(generated_root / str(entry["doc_path"]))
            if entry.get("source_file"):
                entry["source_file"] = normalize_optional_path(entry["source_file"])
            normalized.append(entry)
    return normalized


def normalize_string_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [path for item in value if item for path in [normalize_optional_path(item)] if path is not None]


def value_from(primary: dict[str, Any] | None, fallback: dict[str, Any] | None, key: str) -> Any:
    if isinstance(primary, dict) and primary.get(key) is not None:
        return primary.get(key)
    if isinstance(fallback, dict):
        return fallback.get(key)
    return None


def count_list(payload: dict[str, Any] | None, key: str) -> int:
    value = payload.get(key) if isinstance(payload, dict) else None
    return len(value) if isinstance(value, list) else 0


def normalize_verdict(value: Any) -> str | None:
    text = str(value or "").lower().strip()
    return text if text in {"pass", "warning", "fail"} else None


def usage_totals(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage_totals") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": int_value(usage.get("prompt_tokens")),
        "completion_tokens": int_value(usage.get("completion_tokens")),
        "total_tokens": int_value(usage.get("total_tokens")),
    }


def deterministic_generated_at(payloads: dict[str, Any]) -> str:
    timestamps: list[str] = []
    for payload in payloads.values():
        if not isinstance(payload, dict):
            continue
        for key in ("generated_at", "updated_at"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                timestamps.append(value)
    if timestamps:
        return sorted(timestamps)[-1]
    return datetime.now(timezone.utc).isoformat()


def module_sort_key(module: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        {"high": 0, "medium": 1, "low": 2}.get(str(module.get("priority") or ""), 3),
        {"full": 0, "summary": 1, "skip": 2}.get(str(module.get("explain_mode") or ""), 3),
        str(module.get("module_page_role") or ""),
        str(module.get("name") or ""),
    )


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_outputs(output_root: Path, outputs: dict[str, dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / CURRENT_STATE_FILENAME, outputs["current_state"])
    write_json(output_root / MODULES_INDEX_FILENAME, outputs["modules_index"])
    write_json(output_root / HISTORY_INDEX_FILENAME, outputs["history_index"])
    write_json(output_root / HISTORY_RUNS_FILENAME, outputs["history_runs"])
    write_json(output_root / UI_DATA_MANIFEST_FILENAME, outputs["ui_data_manifest"])


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_optional_path(value: Any) -> str | None:
    if value is None:
        return None
    return normalize_path(Path(str(value)))


def normalize_path(path: Path) -> str:
    try:
        resolved = path.resolve()
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix().replace("\\", "/")


def list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
