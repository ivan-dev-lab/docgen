from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..renderer import determine_module_page_role
from . import config
from .schemas import FileDocRef, GlobalDocRef, ModuleExplainTarget

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
EXPLAIN_MODE_ORDER = {"full": 0, "summary": 1, "skip": 2}


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc.msg} at line {exc.lineno} column {exc.colno}.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object at the top level.")
    return payload


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ensure_analysis_bundle(analysis_dir: Path) -> dict[str, dict[str, Any]]:
    if not analysis_dir.exists():
        raise FileNotFoundError(f"Analysis directory does not exist: {analysis_dir}")
    if not analysis_dir.is_dir():
        raise NotADirectoryError(f"Analysis path is not a directory: {analysis_dir}")

    missing = [name for name in config.REQUIRED_ANALYSIS_ARTIFACTS if not (analysis_dir / name).is_file()]
    if missing:
        raise ValueError("Analysis directory is missing required artifact(s): " + ", ".join(sorted(missing)))

    return {name: load_json(analysis_dir / name) for name in config.REQUIRED_ANALYSIS_ARTIFACTS}


def ensure_docs_inputs(docs_dir: Path) -> tuple[dict[str, Any], list[str]]:
    if not docs_dir.exists():
        raise FileNotFoundError(f"Docs directory does not exist: {docs_dir}")
    if not docs_dir.is_dir():
        raise NotADirectoryError(f"Docs path is not a directory: {docs_dir}")

    manifest_path = docs_dir / "doc-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Docs directory is missing required artifact: doc-manifest.json")

    manifest = load_json(manifest_path)
    warnings: list[str] = []
    layout_version = manifest.get("documentation_layout_version")
    if layout_version != config.EXPECTED_DOCUMENTATION_LAYOUT_VERSION:
        warnings.append(f"expected documentation_layout_version {config.EXPECTED_DOCUMENTATION_LAYOUT_VERSION}")
    return manifest, warnings


def build_global_doc_refs(docs_dir: Path) -> tuple[dict[str, GlobalDocRef], list[str]]:
    refs = {
        "architecture": GlobalDocRef(path="architecture.md", exists=(docs_dir / "architecture.md").is_file()),
        "module_map": GlobalDocRef(path="module-map.md", exists=(docs_dir / "module-map.md").is_file()),
        "dependency_map": GlobalDocRef(path="dependency-map.md", exists=(docs_dir / "dependency-map.md").is_file()),
        "coverage_report": GlobalDocRef(path="coverage-report.md", exists=(docs_dir / "coverage-report.md").is_file()),
        "function_index": GlobalDocRef(
            path="functions/function-index.md",
            exists=(docs_dir / "functions" / "function-index.md").is_file(),
        ),
        "file_index": GlobalDocRef(path="files/index.md", exists=(docs_dir / "files" / "index.md").is_file()),
    }
    warnings = [
        f"missing docs entry: {ref.path}"
        for ref in refs.values()
        if not ref.exists
    ]
    if not (docs_dir / "modules").is_dir():
        warnings.append("missing docs entry: modules/")
    return refs, sorted(warnings)


def build_legacy_doc_slug_map(bundle: dict[str, dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    module_candidates = bundle["module-candidates.json"].get("candidates", [])
    file_paths = unique_strings(
        [
            str(entity.get("file"))
            for entity in bundle["function-index.json"].get("entities", [])
            if entity.get("file")
        ]
        + [
            str(import_entry.get("source_file"))
            for import_entry in bundle["dependency-graph.json"].get("imports", [])
            if import_entry.get("source_file")
        ]
    )

    module_records = [dict(candidate) for candidate in module_candidates if isinstance(candidate, dict)]
    assign_slugs(
        module_records,
        base_source_getter=lambda item: f"module-{item.get('type', 'unknown')}-{item.get('name', 'unknown')}",
        fingerprint_getter=module_fingerprint,
    )
    module_slug_map = {
        module_identity(record): f"modules/{record['slug']}.md"
        for record in module_records
    }

    file_records = [{"path": path} for path in file_paths]
    assign_slugs(
        file_records,
        base_source_getter=lambda item: f"file-{item.get('path', 'unknown')}",
        fingerprint_getter=lambda item: str(item.get("path") or ""),
    )
    file_slug_map = {
        str(record["path"]): f"files/{record['slug']}.md"
        for record in file_records
    }
    return module_slug_map, file_slug_map


def normalize_doc_path(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "/").strip().lstrip("./")


def build_manifest_doc_maps(
    docs_manifest: dict[str, Any],
    docs_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], bool, bool]:
    file_pages_payload = docs_manifest.get("file_pages")
    module_pages_payload = docs_manifest.get("module_pages")
    manifest_has_file_pages = isinstance(file_pages_payload, list)
    manifest_has_module_pages = isinstance(module_pages_payload, list)

    file_doc_map: dict[str, dict[str, Any]] = {}
    if manifest_has_file_pages:
        for entry in file_pages_payload:
            if not isinstance(entry, dict):
                continue
            source_file = str(entry.get("source_file") or "")
            doc_path = normalize_doc_path(entry.get("doc_path"))
            if not source_file or not doc_path:
                continue
            file_doc_map[source_file] = {
                "source_file": source_file,
                "doc_path": doc_path,
                "exists": (docs_dir / Path(doc_path)).is_file(),
                "entity_count": int(entry.get("entity_count") or 0),
                "import_count": int(entry.get("import_count") or 0),
            }

    module_doc_map: dict[str, dict[str, Any]] = {}
    if manifest_has_module_pages:
        for entry in module_pages_payload:
            if not isinstance(entry, dict):
                continue
            doc_path = normalize_doc_path(entry.get("doc_path"))
            if not doc_path:
                continue
            module_doc_map[module_identity(entry)] = {
                "name": str(entry.get("name") or "unknown"),
                "type": str(entry.get("type") or "unknown"),
                "module_page_role": str(
                    entry.get("module_page_role")
                    or determine_module_page_role(str(entry.get("type") or "unknown"))
                ),
                "doc_path": doc_path,
                "exists": (docs_dir / Path(doc_path)).is_file(),
                "files": sorted(unique_strings([str(path) for path in entry.get("files", [])])),
                "source_files": sorted(unique_strings([str(path) for path in entry.get("source_files", [])])),
                "test_files": sorted(unique_strings([str(path) for path in entry.get("test_files", [])])),
                "related_files": sorted(unique_strings([str(path) for path in entry.get("related_files", [])])),
            }

    return file_doc_map, module_doc_map, manifest_has_file_pages, manifest_has_module_pages


def build_effective_doc_maps(
    bundle: dict[str, dict[str, Any]],
    docs_manifest: dict[str, Any],
    docs_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], bool, bool, list[str], list[str]]:
    file_doc_map, module_doc_map, manifest_has_file_pages, manifest_has_module_pages = build_manifest_doc_maps(
        docs_manifest,
        docs_dir,
    )
    warnings: list[str] = []
    mismatches: list[str] = []

    if not manifest_has_file_pages:
        warnings.append("manifest_file_pages_missing_fallback_to_slug")
        mismatches.append("doc-manifest.json missing file_pages mapping; fallback legacy slug logic used")
        _, legacy_file_slug_map = build_legacy_doc_slug_map(bundle)
        for source_file, doc_path in sorted(legacy_file_slug_map.items()):
            file_doc_map.setdefault(
                source_file,
                {
                    "source_file": source_file,
                    "doc_path": doc_path,
                    "exists": (docs_dir / Path(doc_path)).is_file(),
                    "entity_count": 0,
                    "import_count": 0,
                },
            )

    if not manifest_has_module_pages:
        warnings.append("manifest_module_pages_missing_fallback_to_slug")
        mismatches.append("doc-manifest.json missing module_pages mapping; fallback legacy slug logic used")
        legacy_module_slug_map, _ = build_legacy_doc_slug_map(bundle)
        for module in bundle["module-candidates.json"].get("candidates", []):
            if not isinstance(module, dict):
                continue
            identity = module_identity(module)
            doc_path = legacy_module_slug_map.get(identity)
            if not doc_path:
                continue
            module_doc_map.setdefault(
                identity,
                {
                    "name": str(module.get("name") or "unknown"),
                    "type": str(module.get("type") or "unknown"),
                    "module_page_role": determine_module_page_role(str(module.get("type") or "unknown")),
                    "doc_path": doc_path,
                    "exists": (docs_dir / Path(doc_path)).is_file(),
                    "files": sorted(unique_strings([str(path) for path in module.get("files", [])])),
                    "source_files": sorted(unique_strings([str(path) for path in module.get("source_files", [])])),
                    "test_files": sorted(unique_strings([str(path) for path in module.get("test_files", [])])),
                    "related_files": sorted(unique_strings([str(path) for path in module.get("related_files", [])])),
                },
            )

    return (
        file_doc_map,
        module_doc_map,
        manifest_has_file_pages,
        manifest_has_module_pages,
        sorted(unique_strings(warnings)),
        sorted(unique_strings(mismatches)),
    )


def build_analysis_files_with_facts(
    entities: list[dict[str, Any]],
    imports: list[dict[str, Any]],
) -> list[str]:
    return sorted(
        unique_strings(
            [str(entity.get("file") or "") for entity in entities if entity.get("file")]
            + [str(import_entry.get("source_file") or "") for import_entry in imports if import_entry.get("source_file")]
        )
    )


def build_file_doc_refs(
    file_paths: list[str],
    file_doc_map: dict[str, dict[str, Any]],
    docs_dir: Path,
    analysis_files_with_facts: set[str],
    *,
    explain_mode: str,
) -> tuple[list[FileDocRef], list[str], list[str], list[str]]:
    refs: list[FileDocRef] = []
    warnings: list[str] = []
    missing_context: list[str] = []
    mismatches: list[str] = []
    for file_path in sorted(unique_strings(file_paths)):
        entry = file_doc_map.get(file_path)
        if entry is None:
            if file_path not in analysis_files_with_facts:
                continue
            mismatch = f"analysis_render_mismatch: missing file page for {file_path}"
            mismatches.append(mismatch)
            warnings.append(mismatch)
            refs.append(FileDocRef(source_file=file_path, doc_path="", exists=False))
            if explain_mode != "skip":
                missing_context.append(f"file_doc_missing:{file_path}")
            continue

        doc_path = str(entry.get("doc_path") or "")
        exists = bool(entry.get("exists"))
        refs.append(FileDocRef(source_file=file_path, doc_path=doc_path, exists=exists))
        if not exists and file_path in analysis_files_with_facts:
            warnings.append(f"missing file doc: {doc_path or file_path}")
            if explain_mode != "skip":
                missing_context.append(f"file_doc_missing:{file_path}")
    return (
        refs,
        sorted(unique_strings(warnings)),
        sorted(unique_strings(missing_context)),
        sorted(unique_strings(mismatches)),
    )


def count_entities_for_module(module: dict[str, Any], entities: list[dict[str, Any]]) -> int:
    module_files = set(candidate_files_for_context(module))
    return sum(1 for entity in entities if str(entity.get("file") or "") in module_files)


def count_dependencies_for_module(module: dict[str, Any], imports: list[dict[str, Any]]) -> int:
    module_files = set(candidate_files_for_context(module))
    return sum(1 for import_entry in imports if str(import_entry.get("source_file") or "") in module_files)


def candidate_files_for_context(module: dict[str, Any]) -> list[str]:
    return unique_strings(
        [str(path) for path in module.get("source_files", [])]
        + [str(path) for path in module.get("test_files", [])]
    )


def classify_module_plan(
    module: dict[str, Any],
    *,
    entity_count: int,
    dependency_count: int,
    has_non_test_modules: bool,
) -> tuple[str, str, list[str], list[str]]:
    candidate_type = str(module.get("type") or "unknown")
    role = determine_module_page_role(candidate_type)
    reasons: list[str] = []
    warnings: list[str] = []

    if role == "test":
        reasons.append(f"module_page_role={role}")
        reasons.append(f"type={candidate_type}")
        if has_non_test_modules:
            return "low", "skip", reasons, warnings
        warnings.append("only test/fixture modules are available, downgraded from skip to summary")
        return "low", "summary", reasons, warnings

    if role == "detailed":
        reasons.append(f"module_page_role={role}")
        reasons.append(f"type={candidate_type}")
        reasons.append("production-like detailed module")
        if entity_count >= 20 or dependency_count >= 10:
            reasons.append("entity_count >= 20 or dependency_count >= 10")
            return "high", "full", reasons, warnings
        reasons.append("detailed module kept in full mode despite modest size")
        return "medium", "full", reasons, warnings

    if role in {"entrypoint", "aggregate"}:
        reasons.append(f"module_page_role={role}")
        reasons.append(f"type={candidate_type}")
        if entity_count >= 5:
            reasons.append("entity_count >= 5")
            return "medium", "summary", reasons, warnings
        reasons.append("small structural module")
        return "low", "summary", reasons, warnings

    reasons.append(f"module_page_role={role}")
    reasons.append(f"type={candidate_type}")
    reasons.append("unknown role/type")
    return "low", "summary", reasons, warnings


def estimate_output_tokens(explain_mode: str, entity_count: int, dependency_count: int) -> int:
    if explain_mode == "skip":
        return 0
    if explain_mode == "summary":
        return min(
            config.MAX_OUTPUT_TOKENS_PER_MODULE,
            900 + entity_count * 10 + dependency_count * 8,
        )
    return min(
        config.MAX_OUTPUT_TOKENS_PER_MODULE,
        1400 + entity_count * 16 + dependency_count * 12,
    )


def estimate_tokens_from_chars(char_count: int) -> int:
    return max(0, (char_count + 3) // 4)


def context_doc_char_count(path: Path) -> int:
    return len(load_text(path))


def build_module_target(
    *,
    module: dict[str, Any],
    docs_dir: Path,
    module_doc_map: dict[str, dict[str, Any]],
    file_doc_map: dict[str, dict[str, Any]],
    global_doc_refs: dict[str, GlobalDocRef],
    entities: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    has_non_test_modules: bool,
    analysis_files_with_facts: set[str],
    manifest_has_module_pages: bool,
) -> tuple[ModuleExplainTarget, list[str]]:
    module_identity_key = module_identity(module)
    entity_count = count_entities_for_module(module, entities)
    dependency_count = count_dependencies_for_module(module, imports)
    priority, explain_mode, reasons, classification_warnings = classify_module_plan(
        module,
        entity_count=entity_count,
        dependency_count=dependency_count,
        has_non_test_modules=has_non_test_modules,
    )
    analysis_render_mismatches: list[str] = []

    module_doc_entry = module_doc_map.get(module_identity_key)
    if module_doc_entry is not None:
        resolved_module_doc_path = str(module_doc_entry.get("doc_path") or "")
        module_doc_exists = bool(module_doc_entry.get("exists"))
    else:
        resolved_module_doc_path = ""
        module_doc_exists = False
        if manifest_has_module_pages:
            analysis_render_mismatches.append(
                "analysis_render_mismatch: missing module page for "
                f"{module.get('name') or 'unknown'} ({module.get('type') or 'unknown'})"
            )

    module_files = candidate_files_for_context(module)
    file_doc_refs, file_warnings, missing_context, file_mismatches = build_file_doc_refs(
        module_files,
        file_doc_map,
        docs_dir,
        analysis_files_with_facts,
        explain_mode=explain_mode,
    )
    analysis_render_mismatches.extend(file_mismatches)
    context_paths: list[str] = []
    if module_doc_exists and resolved_module_doc_path:
        context_paths.append(resolved_module_doc_path)
    elif explain_mode != "skip":
        missing_context.append("module_doc_missing")
        if resolved_module_doc_path:
            file_warnings.append(f"missing module doc: {resolved_module_doc_path}")
        elif manifest_has_module_pages:
            file_warnings.append(
                "analysis_render_mismatch: missing module page for "
                f"{module.get('name') or 'unknown'} ({module.get('type') or 'unknown'})"
            )

    if global_doc_refs["dependency_map"].exists:
        context_paths.append(global_doc_refs["dependency_map"].path)
    if global_doc_refs["coverage_report"].exists:
        context_paths.append(global_doc_refs["coverage_report"].path)
    if global_doc_refs["function_index"].exists:
        context_paths.append(global_doc_refs["function_index"].path)
    if global_doc_refs["file_index"].exists:
        context_paths.append(global_doc_refs["file_index"].path)
    for file_doc in file_doc_refs:
        if file_doc.exists and file_doc.doc_path:
            context_paths.append(file_doc.doc_path)

    context_paths = sorted(unique_strings(context_paths))

    char_count = 0
    prompt_overhead_chars = estimate_prompt_overhead_chars(explain_mode)
    if module_doc_exists and resolved_module_doc_path:
        char_count += context_doc_char_count(docs_dir / Path(resolved_module_doc_path))
    for file_doc in file_doc_refs:
        if file_doc.exists and file_doc.doc_path:
            char_count += context_doc_char_count(docs_dir / Path(file_doc.doc_path))
    for global_key in ("dependency_map", "coverage_report", "file_index"):
        ref = global_doc_refs[global_key]
        if ref.exists:
            char_count += context_doc_char_count(docs_dir / Path(ref.path))
    if global_doc_refs["function_index"].exists:
        char_count += min(context_doc_char_count(docs_dir / Path(global_doc_refs["function_index"].path)), 1200)
    char_count += prompt_overhead_chars
    estimated_input_tokens = estimate_tokens_from_chars(char_count)
    context_reduction_reason = None
    warnings = sorted(unique_strings(classification_warnings + file_warnings + list(module.get("warnings", []))))
    if estimated_input_tokens > config.MAX_INPUT_TOKENS_PER_MODULE:
        context_reduction_reason = (
            "estimated input exceeded max_input_tokens_per_module; context reduction may be required in later stages"
        )
        warnings.append(context_reduction_reason)

    budget_status = compute_budget_status(estimated_input_tokens)
    estimated_output_tokens = estimate_output_tokens(explain_mode, entity_count, dependency_count)
    planned_output_name = Path(resolved_module_doc_path).name if resolved_module_doc_path else (
        safe_slug(f"module-{module.get('type', 'unknown')}-{module.get('name', 'unknown')}") + ".md"
    )

    return (
        ModuleExplainTarget(
            name=str(module.get("name") or "unknown"),
            type=str(module.get("type") or "unknown"),
            module_page_role=determine_module_page_role(str(module.get("type") or "unknown")),
            module_doc_path=resolved_module_doc_path,
            module_doc_exists=module_doc_exists,
            source_files=sorted(unique_strings(str(path) for path in module.get("source_files", []))),
            test_files=sorted(unique_strings(str(path) for path in module.get("test_files", []))),
            file_doc_paths=file_doc_refs,
            entity_count=entity_count,
            dependency_count=dependency_count,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            budget_status=budget_status,
            priority=priority,
            explain_mode=explain_mode,
            reasons=sorted(unique_strings(reasons)),
            warnings=sorted(unique_strings(warnings)),
            missing_context=sorted(unique_strings(missing_context)),
            context_paths=context_paths,
            context_reduction_reason=context_reduction_reason,
            planned_output_path=f"docs/enhanced/modules/{planned_output_name}",
        ),
        sorted(unique_strings(analysis_render_mismatches)),
    )


def estimate_prompt_overhead_chars(explain_mode: str) -> int:
    if explain_mode == "full":
        return 3200
    if explain_mode == "summary":
        return 2200
    return 1200


def compute_budget_status(estimated_input_tokens: int) -> str:
    if estimated_input_tokens > config.MAX_INPUT_TOKENS_PER_MODULE:
        return "over_limit"
    if estimated_input_tokens >= int(config.MAX_INPUT_TOKENS_PER_MODULE * 0.8):
        return "near_limit"
    return "ok"


def module_identity(module: dict[str, Any]) -> str:
    return json.dumps(
        {
            "name": module.get("name"),
            "type": module.get("type"),
            "files": sorted(unique_strings([str(path) for path in module.get("files", [])])),
            "source_files": sorted(unique_strings([str(path) for path in module.get("source_files", [])])),
            "test_files": sorted(unique_strings([str(path) for path in module.get("test_files", [])])),
            "related_files": sorted(unique_strings([str(path) for path in module.get("related_files", [])])),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def module_fingerprint(module: dict[str, Any]) -> str:
    return module_identity(module)


def assign_slugs(items: list[dict[str, Any]], *, base_source_getter, fingerprint_getter) -> None:
    grouped_indices: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        base_slug = safe_slug(str(base_source_getter(item)))
        item["slug_base"] = base_slug
        grouped_indices.setdefault(base_slug, []).append(index)

    used_slugs: set[str] = set()
    for base_slug, indices in sorted(grouped_indices.items()):
        if len(indices) == 1 and base_slug not in used_slugs:
            items[indices[0]]["slug"] = base_slug
            used_slugs.add(base_slug)
            continue

        counter: dict[str, int] = {}
        for index in sorted(indices, key=lambda item_index: str(fingerprint_getter(items[item_index]))):
            item = items[index]
            fingerprint = str(fingerprint_getter(item))
            suffix = short_hash(fingerprint)
            max_base_length = max(1, 80 - len(suffix) - 1)
            trimmed_base = base_slug[:max_base_length].rstrip("-") or base_slug
            slug = f"{trimmed_base}-{suffix}"
            counter[slug] = counter.get(slug, 0) + 1
            if counter[slug] > 1 or slug in used_slugs:
                slug = f"{slug}-{counter[slug]}"
            item["slug"] = slug
            used_slugs.add(slug)


def safe_slug(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lower()
    normalized = "".join(character if character.isalnum() else "-" for character in normalized)
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    normalized = normalized.strip("-")
    if not normalized:
        normalized = "item"
    if len(normalized) > 80:
        normalized = normalized[:80].rstrip("-")
    return normalized or "item"


def short_hash(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def module_target_sort_key(target: ModuleExplainTarget) -> tuple[Any, ...]:
    return (
        PRIORITY_ORDER.get(target.priority, 99),
        EXPLAIN_MODE_ORDER.get(target.explain_mode, 99),
        target.module_page_role,
        target.name,
        target.planned_output_path,
    )


def module_target_to_dict(target: ModuleExplainTarget) -> dict[str, Any]:
    return asdict(target)
