from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
RUN_KINDS = {"generation", "verification"}
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
GENERATION_FIELDS = (
    "status",
    "cache_hit",
    "context_fingerprint",
    "cache_key",
    "output_path",
    "metadata_path",
    "error",
    "estimated_input_tokens",
)
VERIFICATION_FIELDS = (
    "status",
    "verifier_status",
    "structured_output_valid",
    "verdict",
    "cache_hit",
    "verification_cache_key",
    "context_fingerprint",
    "enhanced_markdown_fingerprint",
    "error",
)
ISSUE_COUNT_FIELDS = (
    "weak_claims_count",
    "unsupported_claims_count",
    "missing_factual_support_count",
    "missing_uncertainty_count",
)


class RunDiffError(ValueError):
    pass


def build_run_diff(kind: str, run_a: dict[str, Any], run_b: dict[str, Any]) -> dict[str, Any]:
    if kind not in RUN_KINDS:
        raise RunDiffError(f"Unsupported compare kind: {kind}")
    validate_run_kind(kind, run_a, "run_a")
    validate_run_kind(kind, run_b, "run_b")
    results_a = results_by_module(run_a)
    results_b = results_by_module(run_b)
    modules = sorted(set(results_a) | set(results_b))
    module_diffs = [
        build_generation_module_diff(module, results_a.get(module), results_b.get(module))
        if kind == "generation"
        else build_verification_module_diff(module, results_a.get(module), results_b.get(module))
        for module in modules
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "run_a": run_metadata(run_a),
        "run_b": run_metadata(run_b),
        "summary": generation_summary(run_a, run_b, module_diffs)
        if kind == "generation"
        else verification_summary(run_a, run_b, module_diffs),
        "module_diffs": module_diffs,
        "warnings": [],
    }


def validate_run_kind(kind: str, run: dict[str, Any], label: str) -> None:
    run_kind = run.get("kind")
    if run_kind and run_kind != kind:
        raise RunDiffError(f"{label} kind mismatch: expected {kind}, got {run_kind}")


def run_metadata(run: dict[str, Any]) -> dict[str, Any]:
    selected_modules = run.get("selected_modules") if isinstance(run.get("selected_modules"), list) else []
    return {
        "run_id": run.get("run_id"),
        "generated_at": run.get("generated_at"),
        "provider": run.get("provider"),
        "model": run.get("model"),
        "manifest_path": run.get("manifest_path"),
        "latest_live_run": bool(run.get("latest_live_run")),
        "selected_modules_count": int_value(run.get("selected_modules_count")) or len(selected_modules),
        "usage_totals": usage(run.get("usage_totals")),
    }


def results_by_module(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = run.get("results") if isinstance(run.get("results"), list) else []
    return {
        str(result.get("module")): result
        for result in results
        if isinstance(result, dict) and result.get("module")
    }


def build_generation_module_diff(
    module: str,
    result_a: dict[str, Any] | None,
    result_b: dict[str, Any] | None,
) -> dict[str, Any]:
    changed_fields = changed_fields_for(result_a, result_b, GENERATION_FIELDS)
    return {
        "module": module,
        "change_status": change_status(result_a, result_b, changed_fields),
        "status_a": field(result_a, "status"),
        "status_b": field(result_b, "status"),
        "cache_hit_a": field(result_a, "cache_hit"),
        "cache_hit_b": field(result_b, "cache_hit"),
        "usage_delta": usage_delta(field(result_a, "usage"), field(result_b, "usage")),
        "changed_fields": changed_fields,
    }


def build_verification_module_diff(
    module: str,
    result_a: dict[str, Any] | None,
    result_b: dict[str, Any] | None,
) -> dict[str, Any]:
    changed_fields = changed_fields_for(result_a, result_b, VERIFICATION_FIELDS + ISSUE_COUNT_FIELDS)
    verdict_a = normalize_verdict(field(result_a, "verdict"))
    verdict_b = normalize_verdict(field(result_b, "verdict"))
    return {
        "module": module,
        "change_status": change_status(result_a, result_b, changed_fields),
        "status_a": field(result_a, "status"),
        "status_b": field(result_b, "status"),
        "verifier_status_a": field(result_a, "verifier_status"),
        "verifier_status_b": field(result_b, "verifier_status"),
        "verdict_a": verdict_a,
        "verdict_b": verdict_b,
        "verdict_direction": verdict_direction(verdict_a, verdict_b),
        "issue_count_delta": {
            "weak_claims": int_value(field(result_b, "weak_claims_count")) - int_value(field(result_a, "weak_claims_count")),
            "unsupported_claims": int_value(field(result_b, "unsupported_claims_count"))
            - int_value(field(result_a, "unsupported_claims_count")),
            "missing_factual_support": int_value(field(result_b, "missing_factual_support_count"))
            - int_value(field(result_a, "missing_factual_support_count")),
            "missing_uncertainty": int_value(field(result_b, "missing_uncertainty_count"))
            - int_value(field(result_a, "missing_uncertainty_count")),
        },
        "usage_delta": usage_delta(field(result_a, "usage"), field(result_b, "usage")),
        "changed_fields": changed_fields,
    }


def changed_fields_for(
    result_a: dict[str, Any] | None,
    result_b: dict[str, Any] | None,
    fields: tuple[str, ...],
) -> list[str]:
    if result_a is None:
        return ["module_added"]
    if result_b is None:
        return ["module_removed"]
    changed = [name for name in fields if field(result_a, name) != field(result_b, name)]
    for key in USAGE_KEYS:
        if int_value(nested_usage(result_a, key)) != int_value(nested_usage(result_b, key)):
            changed.append(f"usage.{key}")
    return changed


def change_status(result_a: dict[str, Any] | None, result_b: dict[str, Any] | None, changed_fields: list[str]) -> str:
    if result_a is None:
        return "added"
    if result_b is None:
        return "removed"
    return "changed" if changed_fields else "unchanged"


def generation_summary(run_a: dict[str, Any], run_b: dict[str, Any], module_diffs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = module_change_counts(module_diffs)
    summary.update(
        {
            "generated_count_delta": int_value(run_b.get("generated_count")) - int_value(run_a.get("generated_count")),
            "skipped_cached_count_delta": int_value(run_b.get("skipped_cached_count"))
            - int_value(run_a.get("skipped_cached_count")),
            "failed_count_delta": int_value(run_b.get("failed_count")) - int_value(run_a.get("failed_count")),
            "usage_total_delta": usage_delta(run_a.get("usage_totals"), run_b.get("usage_totals")),
        }
    )
    return summary


def verification_summary(run_a: dict[str, Any], run_b: dict[str, Any], module_diffs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = module_change_counts(module_diffs)
    directions = [diff.get("verdict_direction") for diff in module_diffs]
    summary.update(
        {
            "verdict_improved_count": directions.count("improved"),
            "verdict_worsened_count": directions.count("worsened"),
            "verdict_unchanged_count": directions.count("unchanged"),
            "usage_total_delta": usage_delta(run_a.get("usage_totals"), run_b.get("usage_totals")),
        }
    )
    return summary


def module_change_counts(module_diffs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "modules_added_count": count_status(module_diffs, "added"),
        "modules_removed_count": count_status(module_diffs, "removed"),
        "modules_changed_count": count_status(module_diffs, "changed"),
        "modules_unchanged_count": count_status(module_diffs, "unchanged"),
    }


def count_status(module_diffs: list[dict[str, Any]], status: str) -> int:
    return sum(1 for diff in module_diffs if diff.get("change_status") == status)


def verdict_direction(verdict_a: str | None, verdict_b: str | None) -> str:
    order = {"fail": 0, "warning": 1, "pass": 2}
    if verdict_a not in order or verdict_b not in order:
        return "unknown"
    if order[verdict_b] > order[verdict_a]:
        return "improved"
    if order[verdict_b] < order[verdict_a]:
        return "worsened"
    return "unchanged"


def usage_delta(value_a: Any, value_b: Any) -> dict[str, int]:
    usage_a = usage(value_a)
    usage_b = usage(value_b)
    return {key: usage_b[key] - usage_a[key] for key in USAGE_KEYS}


def usage(value: Any) -> dict[str, int]:
    payload = value if isinstance(value, dict) else {}
    return {key: int_value(payload.get(key)) for key in USAGE_KEYS}


def nested_usage(result: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(result, dict):
        return None
    usage_payload = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return usage_payload.get(key)


def field(result: dict[str, Any] | None, name: str) -> Any:
    return result.get(name) if isinstance(result, dict) else None


def normalize_verdict(value: Any) -> str | None:
    text = str(value or "").lower().strip()
    return text if text in {"pass", "warning", "fail"} else None


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
