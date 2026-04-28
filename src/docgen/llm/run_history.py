from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENERATION_MANIFEST_FILENAME = "llm-batch-run-manifest.json"
VERIFICATION_MANIFEST_FILENAME = "llm-batch-verification-manifest.json"
HISTORY_INDEX_FILENAME = "index.json"
OPS_SUMMARY_JSON_FILENAME = "ops-summary.json"
OPS_SUMMARY_MARKDOWN_FILENAME = "ops-summary.md"
HISTORY_SCHEMA_VERSION = "1.0"


def record_generation_run(output_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return record_live_run(
        kind="generation",
        enhanced_root=output_root,
        current_manifest_path=output_root / GENERATION_MANIFEST_FILENAME,
        manifest=manifest,
    )


def record_verification_run(
    enhanced_root: Path,
    output_root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return record_live_run(
        kind="verification",
        enhanced_root=enhanced_root,
        current_manifest_path=output_root / VERIFICATION_MANIFEST_FILENAME,
        manifest=manifest,
    )


def record_live_run(
    *,
    kind: str,
    enhanced_root: Path,
    current_manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if kind not in {"generation", "verification"}:
        raise ValueError(f"Unsupported run history kind: {kind}")

    history_root = history_directory(enhanced_root, kind)
    history_root.mkdir(parents=True, exist_ok=True)
    current_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    run_manifest = dict(manifest)
    run_id = allocate_run_id(history_root, run_manifest)
    history_path = history_root / f"{run_id}.json"
    history_manifest_path = relative_manifest_path(history_path, enhanced_root)
    run_manifest["run_id"] = run_id
    run_manifest["history_manifest_path"] = history_manifest_path
    run_manifest["latest_live_run"] = True

    write_json(current_manifest_path, run_manifest)
    write_json(history_path, run_manifest)
    update_history_index(enhanced_root, kind, run_manifest, history_manifest_path)
    write_ops_summary(enhanced_root)
    return run_manifest


def history_directory(enhanced_root: Path, kind: str) -> Path:
    return enhanced_root / "history" / kind


def history_index_path(enhanced_root: Path, kind: str) -> Path:
    return history_directory(enhanced_root, kind) / HISTORY_INDEX_FILENAME


def allocate_run_id(history_root: Path, manifest: dict[str, Any]) -> str:
    base = f"{run_timestamp_slug(manifest.get('generated_at'))}-{short_manifest_hash(manifest)}"
    candidate = base
    counter = 2
    while (history_root / f"{candidate}.json").exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def run_timestamp_slug(generated_at: Any) -> str:
    if isinstance(generated_at, str):
        try:
            timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(timezone.utc)
    else:
        timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def short_manifest_hash(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:8]


def update_history_index(
    enhanced_root: Path,
    kind: str,
    manifest: dict[str, Any],
    manifest_path: str,
) -> dict[str, Any]:
    path = history_index_path(enhanced_root, kind)
    index = load_json(path) or {}
    runs = index.get("runs") if isinstance(index.get("runs"), list) else []
    run_id = str(manifest.get("run_id") or "")
    entry = build_history_index_entry(kind, manifest, manifest_path)
    deduped = [run for run in runs if not (isinstance(run, dict) and run.get("run_id") == run_id)]
    index = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "updated_at": timestamp_now(),
        "kind": kind,
        "runs": [entry, *deduped],
    }
    write_json(path, index)
    return index


def build_history_index_entry(kind: str, manifest: dict[str, Any], manifest_path: str) -> dict[str, Any]:
    common = {
        "run_id": manifest.get("run_id"),
        "generated_at": manifest.get("generated_at"),
        "manifest_path": manifest_path,
        "dry_run": bool(manifest.get("dry_run")),
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "selected_modules": manifest.get("selected_modules") if isinstance(manifest.get("selected_modules"), list) else [],
        "usage_totals": usage_totals(manifest),
    }
    if kind == "generation":
        return {
            **common,
            "generated_count": int_value(manifest.get("generated_count")),
            "skipped_cached_count": int_value(manifest.get("skipped_cached_count")),
            "skipped_by_plan_count": int_value(manifest.get("skipped_by_plan_count")),
            "failed_count": int_value(manifest.get("failed_count")),
        }
    return {
        **common,
        "verified_count": int_value(manifest.get("verified_count")),
        "warning_count": int_value(manifest.get("warning_count")),
        "failed_count": int_value(manifest.get("failed_count")),
        "skipped_cached_count": int_value(manifest.get("skipped_cached_count")),
        "skipped_missing_enhanced_count": int_value(manifest.get("skipped_missing_enhanced_count")),
    }


def write_ops_summary(enhanced_root: Path) -> dict[str, Any]:
    generation_manifest = load_json(enhanced_root / GENERATION_MANIFEST_FILENAME)
    verification_manifest = load_json(enhanced_root / "verification" / VERIFICATION_MANIFEST_FILENAME)
    generation_index = load_json(history_index_path(enhanced_root, "generation")) or {}
    verification_index = load_json(history_index_path(enhanced_root, "verification")) or {}

    summary = build_ops_summary(
        generation_manifest if isinstance(generation_manifest, dict) else None,
        verification_manifest if isinstance(verification_manifest, dict) else None,
        generation_index if isinstance(generation_index, dict) else {},
        verification_index if isinstance(verification_index, dict) else {},
    )
    write_json(enhanced_root / OPS_SUMMARY_JSON_FILENAME, summary)
    (enhanced_root / OPS_SUMMARY_MARKDOWN_FILENAME).write_text(
        render_ops_summary_markdown(summary),
        encoding="utf-8",
    )
    return summary


def build_ops_summary(
    generation_manifest: dict[str, Any] | None,
    verification_manifest: dict[str, Any] | None,
    generation_index: dict[str, Any],
    verification_index: dict[str, Any],
) -> dict[str, Any]:
    warnings = collect_warnings(generation_manifest) + collect_warnings(verification_manifest)
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "generated_at": timestamp_now(),
        "latest_generation_run_id": generation_manifest.get("run_id") if generation_manifest else None,
        "latest_verification_run_id": verification_manifest.get("run_id") if verification_manifest else None,
        "generation_history_count": len(generation_index.get("runs") or []),
        "verification_history_count": len(verification_index.get("runs") or []),
        "latest_generation_counts": generation_counts(generation_manifest),
        "latest_verification_counts": verification_counts(verification_manifest),
        "latest_generation_usage_totals": usage_totals(generation_manifest),
        "latest_verification_usage_totals": usage_totals(verification_manifest),
        "latest_generation_cache_hit_rate": cache_hit_rate(generation_manifest),
        "latest_verification_cache_hit_rate": cache_hit_rate(verification_manifest),
        "warnings": warnings,
    }


def generation_counts(manifest: dict[str, Any] | None) -> dict[str, int] | None:
    if not manifest:
        return None
    return {
        "generated": int_value(manifest.get("generated_count")),
        "skipped_cached": int_value(manifest.get("skipped_cached_count")),
        "skipped_by_plan": int_value(manifest.get("skipped_by_plan_count")),
        "failed": int_value(manifest.get("failed_count")),
    }


def verification_counts(manifest: dict[str, Any] | None) -> dict[str, int] | None:
    if not manifest:
        return None
    return {
        "verified": int_value(manifest.get("verified_count")),
        "warning": int_value(manifest.get("warning_count")),
        "failed": int_value(manifest.get("failed_count")),
        "skipped_cached": int_value(manifest.get("skipped_cached_count")),
        "skipped_missing_enhanced": int_value(manifest.get("skipped_missing_enhanced_count")),
    }


def usage_totals(manifest: dict[str, Any] | None) -> dict[str, int]:
    usage = manifest.get("usage_totals") if isinstance(manifest, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": int_value(usage.get("prompt_tokens")),
        "completion_tokens": int_value(usage.get("completion_tokens")),
        "total_tokens": int_value(usage.get("total_tokens")),
    }


def cache_hit_rate(manifest: dict[str, Any] | None) -> float | None:
    if not manifest:
        return None
    selected = int_value(manifest.get("total_modules_selected"))
    if selected <= 0:
        return None
    return round(int_value(manifest.get("skipped_cached_count")) / selected, 4)


def collect_warnings(manifest: dict[str, Any] | None) -> list[str]:
    if not manifest or not isinstance(manifest.get("warnings"), list):
        return []
    return [str(item) for item in manifest.get("warnings") if str(item)]


def render_ops_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Ops Summary",
        "",
        "## Latest generation run",
        render_generation_section(summary),
        "",
        "## Latest verification run",
        render_verification_section(summary),
        "",
        "## History counts",
        f"- Generation: {summary.get('generation_history_count', 0)}",
        f"- Verification: {summary.get('verification_history_count', 0)}",
        "",
        "## Cache effectiveness",
        f"- Generation cache hit rate: {format_rate(summary.get('latest_generation_cache_hit_rate'))}",
        f"- Verification cache hit rate: {format_rate(summary.get('latest_verification_cache_hit_rate'))}",
        "",
        "## Recent warnings",
    ]
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("нет данных")
    lines.append("")
    return "\n".join(lines)


def render_generation_section(summary: dict[str, Any]) -> str:
    run_id = summary.get("latest_generation_run_id")
    counts = summary.get("latest_generation_counts")
    usage = summary.get("latest_generation_usage_totals")
    if not run_id or not isinstance(counts, dict):
        return "нет данных"
    return (
        f"- run_id: {run_id}\n"
        f"- generated: {counts.get('generated', 0)}\n"
        f"- skipped_cached: {counts.get('skipped_cached', 0)}\n"
        f"- skipped_by_plan: {counts.get('skipped_by_plan', 0)}\n"
        f"- failed: {counts.get('failed', 0)}\n"
        f"- usage_totals: {json.dumps(usage, ensure_ascii=False, sort_keys=True)}"
    )


def render_verification_section(summary: dict[str, Any]) -> str:
    run_id = summary.get("latest_verification_run_id")
    counts = summary.get("latest_verification_counts")
    usage = summary.get("latest_verification_usage_totals")
    if not run_id or not isinstance(counts, dict):
        return "нет данных"
    return (
        f"- run_id: {run_id}\n"
        f"- verified: {counts.get('verified', 0)}\n"
        f"- warning: {counts.get('warning', 0)}\n"
        f"- failed: {counts.get('failed', 0)}\n"
        f"- skipped_cached: {counts.get('skipped_cached', 0)}\n"
        f"- skipped_missing_enhanced: {counts.get('skipped_missing_enhanced', 0)}\n"
        f"- usage_totals: {json.dumps(usage, ensure_ascii=False, sort_keys=True)}"
    )


def format_rate(value: Any) -> str:
    if value is None:
        return "нет данных"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "нет данных"


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def relative_manifest_path(path: Path, enhanced_root: Path) -> str:
    resolved_path = path.resolve()
    for base in relative_path_bases(enhanced_root):
        try:
            return resolved_path.relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def relative_path_bases(enhanced_root: Path) -> list[Path]:
    roots = [Path.cwd()]
    try:
        if enhanced_root.name == "enhanced" and enhanced_root.parent.name == "docs":
            roots.append(enhanced_root.parent.parent)
        roots.append(enhanced_root.parent)
    except IndexError:
        pass
    return roots


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()
