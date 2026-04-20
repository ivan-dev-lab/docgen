from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

SCHEMA_VERSION = "1.0"
RENDERER_VERSION = __version__
DOCUMENTATION_LAYOUT_VERSION = "2.1"
NO_DATA = "нет данных"
NOT_DETERMINED = "не определено"
MODULE_PURPOSE_FALLBACK = (
    "Назначение не определено автоматически. Ниже приведена структурная информация, "
    "извлеченная из JSON-артефактов анализа."
)
REQUIRED_ARTIFACTS = (
    "inventory.json",
    "function-index.json",
    "dependency-graph.json",
    "module-candidates.json",
    "analysis-summary.json",
    "artifact-manifest.json",
    "coverage-report.json",
)
MODULE_TYPE_ORDER = {
    "entrypoint": 0,
    "package": 1,
    "directory": 2,
    "feature": 3,
    "name_cluster": 4,
    "test_target": 5,
    "fixture": 6,
    "test_asset": 7,
    "unknown": 8,
}
MODULE_PAGE_ROLE_ORDER = {
    "entrypoint": 0,
    "detailed": 1,
    "aggregate": 2,
    "test": 3,
    "unknown": 4,
}
KEY_ENTITY_CONFIDENCE_ORDER = {
    "high": 0,
    "medium": 1,
    "low": 2,
}
KEY_ENTITY_TYPE_ORDER = {
    "class": 0,
    "function": 1,
    "method": 1,
}
MODULE_ENTITY_LIMITS = {
    "detailed": 30,
    "aggregate": 15,
    "entrypoint": 15,
    "test": 15,
    "unknown": 15,
}
FILE_ENTITY_LIMIT = 30
MAX_SLUG_LENGTH = 80


def render_project(
    analysis_dir: Path,
    output_dir: Path,
    *,
    analysis_path_label: str | None = None,
    output_path_label: str | None = None,
) -> Path:
    bundle, warnings = load_analysis_bundle(analysis_dir)
    normalize_analysis_bundle(bundle, warnings)

    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_generated_files(output_dir)

    analysis_summary = bundle["analysis-summary.json"]
    coverage_report = bundle["coverage-report.json"]
    inventory = bundle["inventory.json"]
    function_index = bundle["function-index.json"]
    dependency_graph = bundle["dependency-graph.json"]
    module_candidates = bundle["module-candidates.json"]

    generated_at = timestamp_now()
    inventory_files = sort_inventory_files(inventory["files"])
    inventory_by_path = {str(entry.get("path")): entry for entry in inventory_files if isinstance(entry, dict)}
    entities = sort_entities(function_index["entities"])
    imports = sort_imports(dependency_graph["imports"])
    modules = enrich_module_candidates(sort_modules(module_candidates["candidates"]), warnings)
    file_to_modules = build_file_to_modules(modules)
    file_pages = build_file_pages(entities, imports, file_to_modules, warnings)
    file_page_by_path = {page["path"]: page for page in file_pages}

    rendered_files: dict[Path, str] = {}
    rendered_files[output_dir / "index.md"] = render_index_markdown(
        analysis_summary=analysis_summary,
        coverage_report=coverage_report,
        inventory=inventory,
        entities=entities,
        dependency_graph=dependency_graph,
        modules=modules,
        file_pages=file_pages,
        generated_at=generated_at,
    )
    rendered_files[output_dir / "architecture.md"] = render_architecture_markdown(
        analysis_summary=analysis_summary,
        coverage_report=coverage_report,
        inventory=inventory,
        modules=modules,
        analysis_path_label=analysis_path_label or str(analysis_dir),
    )
    rendered_files[output_dir / "module-map.md"] = render_module_map_markdown(modules)
    rendered_files[output_dir / "dependency-map.md"] = render_dependency_map_markdown(
        dependency_graph=dependency_graph,
        imports=imports,
    )
    rendered_files[output_dir / "coverage-report.md"] = render_coverage_markdown(coverage_report)
    rendered_files[output_dir / "functions" / "function-index.md"] = render_functions_markdown(file_pages)
    rendered_files[output_dir / "files" / "index.md"] = render_file_index_markdown(file_pages)

    for file_page in file_pages:
        rendered_files[output_dir / "files" / f"{file_page['slug']}.md"] = render_file_page_markdown(file_page)

    for module in modules:
        rendered_files[output_dir / "modules" / f"{module['slug']}.md"] = render_module_markdown(
            module=module,
            entities=entities,
            imports=imports,
            coverage_report=coverage_report,
            inventory_by_path=inventory_by_path,
            file_to_modules=file_to_modules,
            file_page_by_path=file_page_by_path,
        )

    generated_files = sorted(relative_output_path(path, output_dir) for path in rendered_files)
    doc_manifest = build_doc_manifest(
        generated_at=generated_at,
        analysis_path_label=analysis_path_label or str(analysis_dir),
        output_path_label=output_path_label or str(output_dir),
        generated_files=generated_files + ["doc-manifest.json"],
        module_count=len(modules),
        module_page_count=len(modules),
        file_page_count=len(file_pages),
        entity_count=len(entities),
        dependency_count=len(imports),
        warnings=warnings,
    )
    rendered_files[output_dir / "doc-manifest.json"] = dump_json(doc_manifest)

    for path, content in rendered_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    return output_dir


def load_analysis_bundle(analysis_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not analysis_dir.exists():
        raise FileNotFoundError(f"Analysis directory does not exist: {analysis_dir}")
    if not analysis_dir.is_dir():
        raise NotADirectoryError(f"Analysis path is not a directory: {analysis_dir}")

    missing_artifacts = [name for name in REQUIRED_ARTIFACTS if not (analysis_dir / name).is_file()]
    if missing_artifacts:
        raise ValueError(
            "Analysis directory is missing required artifact(s): "
            + ", ".join(sorted(missing_artifacts))
        )

    bundle: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for artifact_name in REQUIRED_ARTIFACTS:
        artifact_path = analysis_dir / artifact_name
        try:
            data = json.loads(artifact_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {artifact_name}: {exc.msg} at line {exc.lineno} column {exc.colno}."
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(f"{artifact_name} must contain a JSON object at the top level.")
        bundle[artifact_name] = data
    return bundle, warnings


def normalize_analysis_bundle(
    bundle: dict[str, dict[str, Any]],
    warnings: list[str],
) -> None:
    inventory = bundle["inventory.json"]
    inventory["files"] = normalize_list_field(inventory, "files", "inventory.json", warnings)
    for entry in inventory["files"]:
        if not isinstance(entry, dict):
            continue
        entry.setdefault("path", NO_DATA)
        entry.setdefault("file_type", None)
        entry.setdefault("language", None)
        entry.setdefault("artifact_role", None)

    function_index = bundle["function-index.json"]
    function_index["entities"] = normalize_list_field(function_index, "entities", "function-index.json", warnings)
    for entity in function_index["entities"]:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("entity_type") or entity.get("type") or "unknown"
        entity["entity_type"] = entity_type
        entity["type"] = entity.get("type") or entity_type
        entity["parameters"] = entity.get("parameters") if isinstance(entity.get("parameters"), list) else []
        entity["decorators"] = entity.get("decorators") if isinstance(entity.get("decorators"), list) else []
        entity.setdefault("file", NO_DATA)
        entity.setdefault("name", NO_DATA)

    dependency_graph = bundle["dependency-graph.json"]
    dependency_graph["imports"] = normalize_list_field(
        dependency_graph,
        "imports",
        "dependency-graph.json",
        warnings,
    )
    dependency_graph["external_dependencies"] = normalize_list_field(
        dependency_graph,
        "external_dependencies",
        "dependency-graph.json",
        warnings,
    )
    for import_entry in dependency_graph["imports"]:
        if not isinstance(import_entry, dict):
            continue
        import_entry.setdefault("source_file", NO_DATA)
        import_entry.setdefault("imported", NO_DATA)
        import_entry.setdefault("dependency_type", "unknown")

    module_candidates = bundle["module-candidates.json"]
    module_candidates["candidates"] = normalize_list_field(
        module_candidates,
        "candidates",
        "module-candidates.json",
        warnings,
    )
    module_candidates["relations"] = normalize_list_field(
        module_candidates,
        "relations",
        "module-candidates.json",
        warnings,
    )
    for candidate in module_candidates["candidates"]:
        if not isinstance(candidate, dict):
            continue
        for field_name in (
            "files",
            "source_files",
            "test_files",
            "config_files",
            "doc_files",
            "other_files",
            "related_files",
            "reasons",
            "warnings",
            "relations",
        ):
            candidate[field_name] = candidate.get(field_name) if isinstance(candidate.get(field_name), list) else []
        candidate["type"] = candidate.get("type") or "unknown"
        candidate["name"] = candidate.get("name") or "unknown"
        if not candidate["files"]:
            candidate["files"] = sorted(
                unique_strings(
                    candidate["source_files"]
                    + candidate["test_files"]
                    + candidate["config_files"]
                    + candidate["doc_files"]
                    + candidate["other_files"]
                    + candidate["related_files"]
                )
            )
        else:
            candidate["files"] = sorted(unique_strings(candidate["files"]))
        for field_name in (
            "source_files",
            "test_files",
            "config_files",
            "doc_files",
            "other_files",
            "related_files",
        ):
            candidate[field_name] = sorted(unique_strings(candidate[field_name]))

    coverage_report = bundle["coverage-report.json"]
    for field_name in (
        "unsupported_deep_extensions",
        "supported_languages",
        "detected_supported_languages",
        "limitations",
    ):
        coverage_report[field_name] = normalize_list_field(
            coverage_report,
            field_name,
            "coverage-report.json",
            warnings,
        )

    summary = bundle["analysis-summary.json"]
    for field_name in ("detected_languages", "applied_analyzers", "limitations"):
        summary[field_name] = normalize_list_field(summary, field_name, "analysis-summary.json", warnings)


def normalize_list_field(
    data: dict[str, Any],
    field_name: str,
    artifact_name: str,
    warnings: list[str],
) -> list[Any]:
    value = data.get(field_name)
    if value is None:
        warnings.append(f"{artifact_name}: field '{field_name}' missing, using an empty list fallback.")
        return []
    if not isinstance(value, list):
        warnings.append(f"{artifact_name}: field '{field_name}' is not a list, using an empty list fallback.")
        return []
    return value


def sort_inventory_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(files, key=lambda entry: str(entry.get("path") or ""))


def sort_modules(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=module_sort_key)


def sort_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entities,
        key=lambda entity: (
            str(entity.get("file") or ""),
            str(entity.get("name") or ""),
            line_number_sort_key(entity.get("line_start")),
            str(entity.get("entity_type") or entity.get("type") or ""),
        ),
    )


def sort_imports(imports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        imports,
        key=lambda item: (
            str(item.get("source_file") or ""),
            str(item.get("imported") or ""),
            str(item.get("resolved_file") or ""),
        ),
    )


def build_file_to_modules(modules: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    mapping: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for module in modules:
        for file_path in candidate_all_files(module):
            mapping[file_path].append(module)
    for file_path in list(mapping):
        mapping[file_path] = sorted(mapping[file_path], key=module_sort_key)
    return mapping


def enrich_module_candidates(candidates: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    enriched = [dict(candidate) for candidate in candidates]
    for candidate in enriched:
        candidate["module_page_role"] = determine_module_page_role(str(candidate.get("type") or "unknown"))
    assign_collision_safe_slugs(
        items=enriched,
        base_source_getter=lambda item: f"module-{item.get('type', 'unknown')}-{item.get('name', 'unknown')}",
        fingerprint_getter=candidate_fingerprint,
        warnings=warnings,
        warning_label="module",
    )
    return enriched


def build_file_pages(
    entities: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    file_to_modules: dict[str, list[dict[str, Any]]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    entities_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    imports_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        entities_by_file[str(entity.get("file") or NO_DATA)].append(entity)
    for import_entry in imports:
        imports_by_file[str(import_entry.get("source_file") or NO_DATA)].append(import_entry)

    file_paths = sorted(set(entities_by_file) | set(imports_by_file))
    file_pages: list[dict[str, Any]] = []
    for path in file_paths:
        file_pages.append(
            {
                "path": path,
                "entities": sort_entities(entities_by_file.get(path, [])),
                "imports": sort_imports(imports_by_file.get(path, [])),
                "modules": file_to_modules.get(path, []),
            }
        )

    assign_collision_safe_slugs(
        items=file_pages,
        base_source_getter=lambda item: f"file-{item.get('path', 'unknown')}",
        fingerprint_getter=lambda item: str(item.get("path") or ""),
        warnings=warnings,
        warning_label="file",
    )
    return file_pages


def assign_collision_safe_slugs(
    *,
    items: list[dict[str, Any]],
    base_source_getter,
    fingerprint_getter,
    warnings: list[str],
    warning_label: str,
) -> None:
    grouped_indices: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        base_slug = safe_slug(str(base_source_getter(item)))
        item["slug_base"] = base_slug
        grouped_indices[base_slug].append(index)

    used_slugs: set[str] = set()
    for base_slug, indices in sorted(grouped_indices.items()):
        if len(indices) == 1 and base_slug not in used_slugs:
            slug = base_slug
            items[indices[0]]["slug"] = slug
            used_slugs.add(slug)
            continue

        warnings.append(
            f"{warning_label} slug collision detected for '{base_slug}', adding deterministic hash suffixes."
        )
        slug_counts: dict[str, int] = defaultdict(int)
        for index in sorted(indices, key=lambda current: str(fingerprint_getter(items[current]))):
            item = items[index]
            suffix = short_hash(str(fingerprint_getter(item)))
            max_base_length = max(1, MAX_SLUG_LENGTH - len(suffix) - 1)
            trimmed_base = base_slug[:max_base_length].rstrip("-") or base_slug
            slug = f"{trimmed_base}-{suffix}"
            slug_counts[slug] += 1
            if slug_counts[slug] > 1 or slug in used_slugs:
                slug = f"{slug}-{slug_counts[slug]}"
            item["slug"] = slug
            used_slugs.add(slug)


def render_index_markdown(
    *,
    analysis_summary: dict[str, Any],
    coverage_report: dict[str, Any],
    inventory: dict[str, Any],
    entities: list[dict[str, Any]],
    dependency_graph: dict[str, Any],
    modules: list[dict[str, Any]],
    file_pages: list[dict[str, Any]],
    generated_at: str,
) -> str:
    project_name = detect_project_name(analysis_summary)
    dependency_counts = dependency_type_counts(dependency_graph)
    unresolved_unknown = dependency_counts.get("unresolved", 0) + dependency_counts.get("unknown", 0)
    lines = [
        f"# Документация проекта: {project_name}",
        "",
        f"Дата генерации: `{generated_at}`",
        "",
        "## Режим анализа",
        "",
        f"- Tests included: {render_analysis_mode_value(resolve_analysis_mode_value(analysis_summary, coverage_report, 'tests'))}",
        f"- Fixtures included: {render_analysis_mode_value(resolve_analysis_mode_value(analysis_summary, coverage_report, 'fixtures'))}",
        f"- Generated files included: {render_analysis_mode_value(resolve_analysis_mode_value(analysis_summary, coverage_report, 'generated'))}",
        "",
        "## Краткая сводка",
        "",
        f"- Файлов: {fallback_number(analysis_summary.get('file_count'), len(inventory.get('files', [])))}",
        f"- Source-файлов: {fallback_number(analysis_summary.get('source_file_count'))}",
        f"- Test-файлов: {fallback_number(analysis_summary.get('test_file_count'))}",
        f"- Config-файлов: {fallback_number(analysis_summary.get('config_file_count'))}",
        f"- Найденных сущностей: {fallback_number(analysis_summary.get('entity_count'), len(entities))}",
        f"- Импортов: {fallback_number(analysis_summary.get('import_count'), len(dependency_graph.get('imports', [])))}",
        f"- Кандидатов модулей: {len(modules)}",
        f"- File pages: {len(file_pages)}",
        f"- `unresolved`/`unknown` imports: {unresolved_unknown}",
        "",
        "## Разделы",
        "",
        "- [Архитектура](architecture.md)",
        "- [Карта модулей](module-map.md)",
        "- [Карта зависимостей](dependency-map.md)",
        "- [Покрытие анализа](coverage-report.md)",
        "- [Индекс функций, классов и методов](functions/function-index.md)",
        "- [Индекс файлов](files/index.md)",
        "- [Каталог модулей](modules/)",
        "",
        "> Документация создана по результатам статического анализа JSON-артефактов. "
        "Она не гарантирует полноту runtime-поведения и не заменяет ручное архитектурное описание.",
        "",
    ]
    return "\n".join(lines)


def render_architecture_markdown(
    *,
    analysis_summary: dict[str, Any],
    coverage_report: dict[str, Any],
    inventory: dict[str, Any],
    modules: list[dict[str, Any]],
    analysis_path_label: str,
) -> str:
    entrypoints = [module for module in modules if module.get("type") == "entrypoint"]
    core_candidates = [
        module
        for module in modules
        if module.get("type") in {"package", "directory", "feature", "name_cluster", "unknown"}
    ]
    test_like_candidates = [
        module
        for module in modules
        if module.get("type") in {"fixture", "test_asset", "test_target"}
    ]
    readme_present = any(entry.get("path") == "README.md" for entry in inventory.get("files", []))

    lines = [
        "# Архитектура проекта",
        "",
        "## Источник данных",
        "",
        f"- `analysis_dir`: `{analysis_path_label}`",
        f"- `schema_version`: `{analysis_summary.get('schema_version') or coverage_report.get('schema_version') or NO_DATA}`",
        f"- `generated_at`: `{analysis_summary.get('generated_at') or NO_DATA}`",
        "",
        "## Общая структура проекта",
        "",
        "### Основные директории и пакеты",
        "",
        render_named_candidate_list(core_candidates),
        "",
        "### Entry point candidates",
        "",
        render_named_candidate_list(entrypoints),
        "",
        "### Test, fixture и test asset candidates",
        "",
        render_named_candidate_list(test_like_candidates),
        "",
        "## Границы анализа",
        "",
        f"- Поддержанные языки: {render_inline_list(coverage_report.get('supported_languages'))}",
        f"- Обнаруженные поддержанные языки: {render_inline_list(coverage_report.get('detected_supported_languages'))}",
        f"- Глубоко проанализированных файлов: {fallback_number(coverage_report.get('deep_analyzed_file_count'))}",
        f"- Поверхностно проиндексированных файлов: {fallback_number(coverage_report.get('shallow_indexed_file_count'))}",
        f"- `unsupported_deep_extensions`: {render_inline_list(coverage_report.get('unsupported_deep_extensions'))}",
        "",
        "### Ограничения текущего анализа",
        "",
        render_bullet_list(coverage_report.get("limitations")),
        "",
        "## Наблюдения по данным inventory",
        "",
        (
            "- `README.md` присутствует в inventory, но render-слой не читает его напрямую."
            if readme_present
            else "- `README.md` не обнаружен в inventory."
        ),
        "- Render-слой строит документацию только по JSON-артефактам анализа и не перечитывает исходные файлы.",
        "",
        "## Что не удалось определить автоматически",
        "",
        "- Бизнес-назначение проекта.",
        "- Runtime-поведение.",
        "- Внешние API.",
        "- Побочные эффекты.",
        "- Динамические импорты.",
        "- Сценарии запуска, если они не представлены в JSON.",
        "",
    ]
    return "\n".join(lines)


def render_module_map_markdown(modules: list[dict[str, Any]]) -> str:
    rows = []
    for module in modules:
        rows.append(
            [
                module.get("name"),
                module.get("type"),
                module.get("module_page_role"),
                module.get("confidence"),
                f"[modules/{module['slug']}.md](modules/{module['slug']}.md)",
                len(module.get("source_files", [])),
                len(module.get("test_files", [])),
            ]
        )

    lines = [
        "# Карта модулей",
        "",
        "## Сводка",
        "",
        render_table(
            headers=["name", "type", "module_page_role", "confidence", "doc", "source_files", "test_files"],
            rows=rows,
        ),
        "",
    ]

    for module in modules:
        lines.extend(
            [
                f"## {module.get('name')}",
                "",
                f"- `type`: `{module.get('type') or NO_DATA}`",
                f"- `module_page_role`: `{module.get('module_page_role') or NO_DATA}`",
                f"- `confidence`: `{module.get('confidence') or NO_DATA}`",
                f"- Документ: [modules/{module['slug']}.md](modules/{module['slug']}.md)",
            ]
        )
        if module.get("module_page_role") == "test":
            lines.append("- Это тестовый/примерный артефакт, а не production-модуль.")
        lines.extend(
            [
                "",
                "### Файлы по категориям",
                "",
                render_categorized_files(module),
                "",
                "### Related files",
                "",
                render_bullet_list(module.get("related_files")),
                "",
                "### Relations",
                "",
                render_relations(module.get("relations")),
                "",
                "### Reasons",
                "",
                render_bullet_list(module.get("reasons")),
                "",
                "### Warnings",
                "",
                render_bullet_list(module.get("warnings")),
                "",
            ]
        )
    return "\n".join(lines)


def render_dependency_map_markdown(
    *,
    dependency_graph: dict[str, Any],
    imports: list[dict[str, Any]],
) -> str:
    counts = dependency_type_counts(dependency_graph, imports)
    third_party_dependencies = sort_external_dependencies(dependency_graph.get("external_dependencies", []), imports)
    unresolved_imports = [
        import_entry
        for import_entry in imports
        if import_entry.get("dependency_type") in {"unresolved", "unknown"}
    ]

    lines = [
        "# Карта зависимостей",
        "",
        "## Сводка по `dependency_type`",
        "",
        f"- `internal`: {counts.get('internal', 0)}",
        f"- `stdlib`: {counts.get('stdlib', 0)}",
        f"- `node_builtin`: {counts.get('node_builtin', 0)}",
        f"- `third_party`: {counts.get('third_party', 0)}",
        f"- `unresolved`: {counts.get('unresolved', 0)}",
        f"- `unknown`: {counts.get('unknown', 0)}",
        "",
        "## Все импорты",
        "",
        render_import_table(imports, include_source_file=True),
        "",
        "## Third-party dependencies",
        "",
        render_table(
            headers=["name", "version", "manifest_type", "ecosystem", "source_file"],
            rows=[
                [
                    dependency.get("name"),
                    dependency.get("version"),
                    dependency.get("manifest_type"),
                    dependency.get("ecosystem"),
                    dependency.get("source_file"),
                ]
                for dependency in third_party_dependencies
            ],
        ),
        "",
        "## Unresolved and unknown imports",
        "",
        render_import_table(unresolved_imports, include_source_file=True),
        "",
        "> Динамические импорты и runtime-generated dependencies могут отсутствовать.",
        "",
    ]
    return "\n".join(lines)


def render_coverage_markdown(coverage_report: dict[str, Any]) -> str:
    lines = [
        "# Покрытие анализа",
        "",
        render_table(
            headers=["field", "value"],
            rows=[
                ["indexed_file_count", coverage_report.get("indexed_file_count")],
                ["deep_analyzed_file_count", coverage_report.get("deep_analyzed_file_count")],
                ["shallow_indexed_file_count", coverage_report.get("shallow_indexed_file_count")],
                ["unsupported_deep_extensions", render_inline_list(coverage_report.get("unsupported_deep_extensions"))],
                ["supported_languages", render_inline_list(coverage_report.get("supported_languages"))],
                [
                    "detected_supported_languages",
                    render_inline_list(coverage_report.get("detected_supported_languages")),
                ],
                ["unresolved_import_count", coverage_report.get("unresolved_import_count")],
                ["low_confidence_entity_count", coverage_report.get("low_confidence_entity_count")],
                ["limitations", render_inline_list(coverage_report.get("limitations"))],
            ],
        ),
        "",
        "## Что эта документация не гарантирует",
        "",
        "- Dynamic imports не гарантируются.",
        "- Runtime-generated dependencies не гарантируются.",
        "- Regex-based JS/TS extraction может быть приблизительным.",
        "- Unknown file types индексируются поверхностно.",
        "- Бизнес-смысл не выводится надежно без ручного описания или LLM-слоя.",
        "",
    ]
    return "\n".join(lines)


def render_functions_markdown(file_pages: list[dict[str, Any]]) -> str:
    lines = [
        "# Индекс функций, классов и методов",
        "",
    ]
    pages_with_entities = [page for page in file_pages if page.get("entities")]
    if not pages_with_entities:
        lines.extend([NO_DATA, ""])
        return "\n".join(lines)

    for page in pages_with_entities:
        entities = page["entities"]
        displayed_entities = select_key_entities(entities, FILE_ENTITY_LIMIT)
        lines.extend(
            [
                f"## {page['path']}",
                "",
                f"- File page: [../files/{page['slug']}.md](../files/{page['slug']}.md)",
                f"- Количество сущностей: {len(entities)}",
                "",
                render_entity_table(displayed_entities, include_file=True),
                "",
            ]
        )
        if len(entities) > FILE_ENTITY_LIMIT:
            lines.extend(
                [
                    f"Показаны первые {FILE_ENTITY_LIMIT} сущностей. Полный список находится в "
                    f"[file page](../files/{page['slug']}.md).",
                    "",
                ]
            )
    return "\n".join(lines)


def render_file_index_markdown(file_pages: list[dict[str, Any]]) -> str:
    lines = [
        "# Индекс файлов",
        "",
        render_table(
            headers=["file path", "entity_count", "import_count", "doc"],
            rows=[
                [
                    page.get("path"),
                    len(page.get("entities", [])),
                    len(page.get("imports", [])),
                    f"[{page['slug']}.md]({page['slug']}.md)",
                ]
                for page in file_pages
            ],
        ),
        "",
    ]
    return "\n".join(lines)


def render_file_page_markdown(file_page: dict[str, Any]) -> str:
    lines = [
        f"# Файл: {file_page.get('path')}",
        "",
        "## Сущности",
        "",
        render_entity_table(file_page.get("entities", []), include_file=False),
        "",
        "## Импорты",
        "",
        render_import_table(file_page.get("imports", []), include_source_file=False),
        "",
        "## Участвует в модулях",
        "",
    ]
    modules = file_page.get("modules", [])
    if not modules:
        lines.extend([NO_DATA, ""])
        return "\n".join(lines)

    for module in modules:
        lines.append(
            f"- [{module.get('name')}](../modules/{module['slug']}.md) "
            f"(`{module.get('type') or 'unknown'}`, `{module.get('module_page_role') or 'unknown'}`)"
        )
    lines.append("")
    return "\n".join(lines)


def render_module_markdown(
    *,
    module: dict[str, Any],
    entities: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    coverage_report: dict[str, Any],
    inventory_by_path: dict[str, dict[str, Any]],
    file_to_modules: dict[str, list[dict[str, Any]]],
    file_page_by_path: dict[str, dict[str, Any]],
) -> str:
    module_files = candidate_all_files(module)
    role = str(module.get("module_page_role") or "unknown")
    related_entities = [entity for entity in entities if str(entity.get("file")) in module_files]
    related_imports = [import_entry for import_entry in imports if str(import_entry.get("source_file")) in module_files]
    related_module_rows = build_related_module_rows(module, related_imports, file_to_modules)
    key_entities = select_key_entities(related_entities, MODULE_ENTITY_LIMITS.get(role, 15))

    lines = [
        f"# Модуль: {module.get('name')}",
        "",
        "## Тип",
        "",
        str(module.get("type") or NO_DATA),
        "",
        "## Роль страницы",
        "",
        role,
        "",
        "## Назначение",
        "",
        MODULE_PURPOSE_FALLBACK,
        "",
        "## Навигация",
        "",
        "- [Индекс функций](../functions/function-index.md)",
        "- [Индекс файлов](../files/index.md)",
        "",
    ]

    lines.extend(render_module_overview_lines(module, role, related_entities, related_imports, inventory_by_path, file_page_by_path))

    lines.extend(
        [
            "## Границы ответственности",
            "",
            render_categorized_files(module),
            "",
            "## Ключевые файлы",
            "",
            render_key_file_table(module, file_page_by_path),
            "",
        ]
    )

    if role == "detailed":
        lines.extend(
            [
                "## Связанные сущности",
                "",
                render_entity_table(key_entities, include_file=True),
                "",
            ]
        )
        if len(related_entities) > MODULE_ENTITY_LIMITS["detailed"]:
            lines.extend(
                [
                    f"Показаны первые {MODULE_ENTITY_LIMITS['detailed']} сущностей. Полный список см. в "
                    "[functions/function-index.md](../functions/function-index.md) и "
                    "[files/index.md](../files/index.md).",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "## Ключевые сущности",
                "",
                render_entity_table(key_entities, include_file=True),
                "",
            ]
        )
        if len(related_entities) > MODULE_ENTITY_LIMITS.get(role, 15):
            lines.extend(
                [
                    f"Показаны первые {MODULE_ENTITY_LIMITS.get(role, 15)} сущностей. Полный список см. в "
                    "[functions/function-index.md](../functions/function-index.md) и "
                    "[files/index.md](../files/index.md).",
                    "",
                ]
            )

    lines.extend(
        [
            "## Зависимости",
            "",
            render_import_table(related_imports, include_source_file=True),
            "",
            "## Связи с другими модулями",
            "",
            (
                render_table(
                    headers=["source_file", "resolved_file", "target_modules", "ambiguous"],
                    rows=related_module_rows,
                )
                if related_module_rows
                else "Связи между модулями не определены автоматически."
            ),
            "",
            "## Предупреждения и ограничения",
            "",
            render_module_warnings(module, coverage_report),
            "",
            "## Что требует ручного уточнения",
            "",
            "- Бизнес-назначение модуля.",
            "- Runtime-поведение.",
            "- Внешние API.",
            "- Побочные эффекты.",
            "- Сценарии использования.",
            "",
        ]
    )
    return "\n".join(lines)


def render_module_overview_lines(
    module: dict[str, Any],
    role: str,
    related_entities: list[dict[str, Any]],
    related_imports: list[dict[str, Any]],
    inventory_by_path: dict[str, dict[str, Any]],
    file_page_by_path: dict[str, dict[str, Any]],
) -> list[str]:
    lines: list[str] = [
        "## Структурная сводка",
        "",
    ]
    primary_files = primary_module_files(module)
    if role == "aggregate":
        lines.extend(
            [
                "Это обзорная aggregate-страница. Она не является полным индексом всех сущностей.",
                "",
                f"- Количество файлов: {len(candidate_all_files(module))}",
                f"- Количество сущностей: {len(related_entities)}",
                f"- Количество импортов: {len(related_imports)}",
                f"- Основные файлы: {render_file_link_list(primary_files, '../files', file_page_by_path)}",
                "- Полный индекс функций: [functions/function-index.md](../functions/function-index.md)",
                "- Индекс файлов: [files/index.md](../files/index.md)",
                "",
            ]
        )
    elif role == "entrypoint":
        entrypoint_files = [
            path
            for path in module.get("source_files", [])
            if inventory_by_path.get(path, {}).get("is_possible_entrypoint")
        ]
        lines.extend(
            [
                "Это entrypoint-страница. Она показывает структурный обзор точки входа, а не полный индекс всех функций.",
                "",
                f"- Entry point files: {render_file_link_list(entrypoint_files, '../files', file_page_by_path)}",
                f"- Связанные файлы: {render_file_link_list(primary_files, '../files', file_page_by_path)}",
                f"- Количество сущностей: {len(related_entities)}",
                f"- Количество импортов: {len(related_imports)}",
                "",
            ]
        )
    elif role == "test":
        lines.extend(
            [
                "Это тестовый/примерный артефакт, а не production-модуль.",
                "",
                f"- Test files: {render_file_link_list(module.get('test_files', []), '../files', file_page_by_path)}",
                f"- Related production/source files: {render_file_link_list(module.get('related_files', []), '../files', file_page_by_path)}",
                f"- Количество сущностей: {len(related_entities)}",
                f"- Количество импортов: {len(related_imports)}",
                "",
            ]
        )
    elif role == "detailed":
        lines.extend(
            [
                f"- Количество файлов: {len(candidate_all_files(module))}",
                f"- Количество сущностей: {len(related_entities)}",
                f"- Количество импортов: {len(related_imports)}",
                f"- Основные файлы: {render_file_link_list(primary_files, '../files', file_page_by_path)}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Это осторожное представление структурных данных. Архитектурные выводы не делаются автоматически.",
                "",
                f"- Количество файлов: {len(candidate_all_files(module))}",
                f"- Количество сущностей: {len(related_entities)}",
                f"- Количество импортов: {len(related_imports)}",
                "",
            ]
        )
    return lines


def render_key_file_table(module: dict[str, Any], file_page_by_path: dict[str, dict[str, Any]]) -> str:
    rows: list[list[Any]] = []
    for category_name in (
        "source_files",
        "test_files",
        "config_files",
        "doc_files",
        "other_files",
        "related_files",
    ):
        for file_path in module.get(category_name, []):
            rows.append([category_name, render_file_link(file_path, "../files", file_page_by_path)])
    return render_table(headers=["category", "file"], rows=rows)


def render_module_warnings(module: dict[str, Any], coverage_report: dict[str, Any]) -> str:
    warnings = list(module.get("warnings") or [])
    if module.get("module_page_role") == "test":
        warnings.append("Это не production-модуль, а тестовый/примерный артефакт.")
    warnings.extend(str(item) for item in coverage_report.get("limitations", []))
    return render_bullet_list(sorted(unique_strings(warnings)))


def render_named_candidate_list(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return NO_DATA
    return "\n".join(
        f"- `{candidate.get('name')}` (`{candidate.get('type') or 'unknown'}`)"
        for candidate in candidates
    )


def render_categorized_files(candidate: dict[str, Any]) -> str:
    lines = [
        f"- `source_files`: {render_file_link_list(candidate.get('source_files', []), '../files', None)}",
        f"- `test_files`: {render_file_link_list(candidate.get('test_files', []), '../files', None)}",
        f"- `config_files`: {render_file_link_list(candidate.get('config_files', []), '../files', None)}",
        f"- `doc_files`: {render_file_link_list(candidate.get('doc_files', []), '../files', None)}",
        f"- `other_files`: {render_file_link_list(candidate.get('other_files', []), '../files', None)}",
        f"- `related_files`: {render_file_link_list(candidate.get('related_files', []), '../files', None)}",
    ]
    return "\n".join(lines)


def render_relations(relations: list[dict[str, Any]] | None) -> str:
    if not relations:
        return NO_DATA
    rows = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        rows.append(
            [
                relation.get("relation_type"),
                relation.get("source"),
                relation.get("target"),
                relation.get("confidence"),
                relation.get("reason"),
            ]
        )
    return render_table(
        headers=["relation_type", "source", "target", "confidence", "reason"],
        rows=rows,
    )


def render_entity_table(entities: list[dict[str, Any]], *, include_file: bool) -> str:
    if not entities:
        return NO_DATA
    headers = [
        "name",
        "entity_type",
        "type",
    ]
    if include_file:
        headers.append("file")
    headers.extend(
        [
            "parent/container",
            "signature",
            "parameters",
            "return_annotation",
            "is_async",
            "exported",
            "docstring/jsdoc",
            "confidence",
        ]
    )
    rows = []
    for entity in entities:
        row = [
            entity.get("name"),
            entity.get("entity_type"),
            entity.get("type"),
        ]
        if include_file:
            row.append(entity.get("file"))
        row.extend(
            [
                combine_parent_container(entity),
                entity.get("signature"),
                render_parameters(entity.get("parameters")),
                entity.get("return_annotation") or entity.get("return_type"),
                entity.get("is_async"),
                entity.get("exported"),
                entity.get("docstring"),
                entity.get("confidence"),
            ]
        )
        rows.append(row)
    return render_table(headers=headers, rows=rows)


def render_import_table(imports: list[dict[str, Any]], *, include_source_file: bool) -> str:
    if not imports:
        return NO_DATA
    headers = []
    if include_source_file:
        headers.append("source_file")
    headers.extend(["imported", "dependency_type", "resolved_file"])
    rows = []
    for import_entry in imports:
        row = []
        if include_source_file:
            row.append(import_entry.get("source_file"))
        row.extend(
            [
                import_entry.get("imported"),
                import_entry.get("dependency_type"),
                import_entry.get("resolved_file"),
            ]
        )
        rows.append(row)
    return render_table(headers=headers, rows=rows)


def render_bullet_list(items: list[Any] | None) -> str:
    if not items:
        return NO_DATA
    return "\n".join(f"- {format_markdown_value(item, in_table=False)}" for item in items)


def render_inline_list(items: list[Any] | None) -> str:
    if not items:
        return NO_DATA
    rendered = [format_markdown_value(item, in_table=False) for item in items if item not in (None, "", [], {})]
    return ", ".join(rendered) if rendered else NO_DATA


def render_file_link(file_path: str, relative_prefix: str, file_page_by_path: dict[str, dict[str, Any]] | None) -> str:
    if file_page_by_path:
        page = file_page_by_path.get(file_path)
        if page:
            return f"[{file_path}]({relative_prefix}/{page['slug']}.md)"
    return file_path or NO_DATA


def render_file_link_list(
    file_paths: list[str] | None,
    relative_prefix: str,
    file_page_by_path: dict[str, dict[str, Any]] | None,
) -> str:
    if not file_paths:
        return NO_DATA
    return ", ".join(render_file_link(path, relative_prefix, file_page_by_path) for path in file_paths)


def build_related_module_rows(
    module: dict[str, Any],
    imports: list[dict[str, Any]],
    file_to_modules: dict[str, list[dict[str, Any]]],
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    seen: set[tuple[str, str, tuple[str, ...], bool]] = set()
    current_slug = module.get("slug")
    for import_entry in imports:
        resolved_file = str(import_entry.get("resolved_file") or "")
        if not resolved_file:
            continue
        member_candidates = file_to_modules.get(resolved_file, [])
        target_candidates = [candidate for candidate in member_candidates if candidate.get("slug") != current_slug]
        if not target_candidates:
            continue
        target_names = tuple(sorted(str(candidate.get("name") or "unknown") for candidate in target_candidates))
        ambiguous = len(member_candidates) > 1
        key = (
            str(import_entry.get("source_file") or ""),
            resolved_file,
            target_names,
            ambiguous,
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            [
                import_entry.get("source_file"),
                resolved_file,
                ", ".join(target_names) if target_names else NO_DATA,
                "true" if ambiguous else "false",
            ]
        )
    return sorted(rows, key=lambda row: (str(row[0]), str(row[1]), str(row[2])))


def render_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return NO_DATA
    table_lines = [
        "| " + " | ".join(escape_table_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        normalized_row = list(row) + [None] * max(0, len(headers) - len(row))
        table_lines.append(
            "| "
            + " | ".join(escape_table_cell(value) for value in normalized_row[: len(headers)])
            + " |"
        )
    return "\n".join(table_lines)


def escape_table_cell(value: Any) -> str:
    rendered = format_markdown_value(value, in_table=True)
    return rendered.replace("|", "\\|")


def format_markdown_value(value: Any, *, in_table: bool) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return NO_DATA
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        rendered = [format_markdown_value(item, in_table=False) for item in value if item not in (None, "", [], {})]
        if not rendered:
            return NO_DATA
        separator = "<br>" if in_table else ", "
        return separator.join(rendered)
    if isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return truncate_markdown_text(text, in_table=in_table)
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return NO_DATA
    text = sanitize_markdown_text(text)
    if in_table:
        text = text.replace("\n", "<br>")
    return truncate_markdown_text(text, in_table=in_table)


def truncate_markdown_text(text: str, *, in_table: bool) -> str:
    limit = 320 if in_table else 1000
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def sanitize_markdown_text(text: str) -> str:
    sanitized = text.replace("is_internal", "is internal")
    sanitized = sanitized.replace("is_external", "is external")
    return sanitized


def determine_module_page_role(candidate_type: str) -> str:
    normalized = candidate_type or "unknown"
    if normalized == "entrypoint":
        return "entrypoint"
    if normalized in {"fixture", "test_asset", "tests", "test_target"}:
        return "test"
    if normalized == "directory":
        return "aggregate"
    if normalized in {"package", "feature", "name_cluster"}:
        return "detailed"
    return "unknown"


def key_entity_sort_key(entity: dict[str, Any]) -> tuple[Any, ...]:
    exported_priority = 0 if entity.get("exported") is True else 1
    confidence_priority = KEY_ENTITY_CONFIDENCE_ORDER.get(str(entity.get("confidence") or "").lower(), 3)
    entity_type_priority = KEY_ENTITY_TYPE_ORDER.get(
        str(entity.get("entity_type") or entity.get("type") or "").lower(),
        2,
    )
    return (
        exported_priority,
        confidence_priority,
        entity_type_priority,
        str(entity.get("file") or ""),
        line_number_sort_key(entity.get("line_start")),
        str(entity.get("name") or ""),
    )


def select_key_entities(entities: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(entities, key=key_entity_sort_key)[:limit]


def sort_external_dependencies(
    external_dependencies: list[dict[str, Any]] | None,
    imports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dependencies = [dependency for dependency in external_dependencies or [] if isinstance(dependency, dict)]
    if dependencies:
        return sorted(
            dependencies,
            key=lambda dependency: (
                str(dependency.get("name") or ""),
                str(dependency.get("manifest_type") or ""),
                str(dependency.get("source_file") or ""),
            ),
        )

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for import_entry in imports:
        if import_entry.get("dependency_type") != "third_party":
            continue
        key = (
            str(import_entry.get("imported") or ""),
            str(import_entry.get("matched_dependency") or ""),
        )
        deduplicated[key] = {
            "name": import_entry.get("matched_dependency") or import_entry.get("imported"),
            "version": None,
            "manifest_type": None,
            "ecosystem": None,
            "source_file": None,
        }
    return sorted(deduplicated.values(), key=lambda dependency: str(dependency.get("name") or ""))


def resolve_analysis_mode_value(
    analysis_summary: dict[str, Any],
    coverage_report: dict[str, Any],
    mode_name: str,
) -> Any:
    candidate_keys = {
        "tests": ("tests_included", "include_tests", "analysis_include_tests"),
        "fixtures": ("fixtures_included", "include_fixtures", "analysis_include_fixtures"),
        "generated": ("generated_files_included", "include_generated", "analysis_include_generated"),
    }[mode_name]
    for source in (analysis_summary, coverage_report):
        for key in candidate_keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def render_analysis_mode_value(value: Any) -> str:
    if value is None:
        return NOT_DETERMINED
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def detect_project_name(analysis_summary: dict[str, Any]) -> str:
    if analysis_summary.get("project_name"):
        return str(analysis_summary["project_name"])
    if analysis_summary.get("project_path"):
        candidate = Path(str(analysis_summary["project_path"]))
        if candidate.name:
            return candidate.name
    return "unknown-project"


def dependency_type_counts(
    dependency_graph: dict[str, Any],
    imports: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    counts = dependency_graph.get("dependency_type_counts")
    if isinstance(counts, dict):
        return {str(key): int(value) for key, value in counts.items()}

    computed: dict[str, int] = defaultdict(int)
    for import_entry in imports or dependency_graph.get("imports", []):
        if not isinstance(import_entry, dict):
            continue
        computed[str(import_entry.get("dependency_type") or "unknown")] += 1
    return dict(computed)


def primary_module_files(module: dict[str, Any], limit: int = 10) -> list[str]:
    ordered: list[str] = []
    for field_name in (
        "source_files",
        "test_files",
        "config_files",
        "doc_files",
        "other_files",
        "related_files",
    ):
        ordered.extend(str(path) for path in module.get(field_name, []))
    return unique_strings(ordered)[:limit]


def render_parameters(parameters: list[dict[str, Any]] | Any) -> str:
    if not isinstance(parameters, list) or not parameters:
        return NO_DATA
    parts = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            parts.append(str(parameter))
            continue
        name = str(parameter.get("name") or "unknown")
        annotation = parameter.get("annotation")
        default = parameter.get("default")
        fragment = name
        if annotation:
            fragment = f"{fragment}: {annotation}"
        if default is not None:
            fragment = f"{fragment} = {default}"
        parts.append(fragment)
    return ", ".join(parts) if parts else NO_DATA


def combine_parent_container(entity: dict[str, Any]) -> str:
    parent = entity.get("parent")
    container = entity.get("container")
    if parent and container:
        return f"{parent} / {container}"
    if parent:
        return str(parent)
    if container:
        return str(container)
    return NO_DATA


def module_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        MODULE_TYPE_ORDER.get(str(candidate.get("type") or "unknown"), 99),
        MODULE_PAGE_ROLE_ORDER.get(determine_module_page_role(str(candidate.get("type") or "unknown")), 99),
        str(candidate.get("type") or "unknown"),
        str(candidate.get("name") or "unknown"),
        tuple(candidate.get("files", [])),
    )


def candidate_all_files(candidate: dict[str, Any]) -> list[str]:
    return sorted(
        unique_strings(
            candidate.get("source_files", [])
            + candidate.get("test_files", [])
            + candidate.get("config_files", [])
            + candidate.get("doc_files", [])
            + candidate.get("other_files", [])
            + candidate.get("related_files", [])
        )
    )


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    canonical = {
        "name": candidate.get("name"),
        "type": candidate.get("type"),
        "files": sorted(candidate.get("files", [])),
        "source_files": sorted(candidate.get("source_files", [])),
        "test_files": sorted(candidate.get("test_files", [])),
        "related_files": sorted(candidate.get("related_files", [])),
    }
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True)


def safe_slug(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    if not normalized:
        normalized = "item"
    if len(normalized) > MAX_SLUG_LENGTH:
        normalized = normalized[:MAX_SLUG_LENGTH].rstrip("-")
    return normalized or "item"


def line_number_sort_key(value: Any) -> int:
    if isinstance(value, int):
        return value
    return 10**9


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relative_output_path(path: Path, output_dir: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def build_doc_manifest(
    *,
    generated_at: str,
    analysis_path_label: str,
    output_path_label: str,
    generated_files: list[str],
    module_count: int,
    module_page_count: int,
    file_page_count: int,
    entity_count: int,
    dependency_count: int,
    warnings: list[str],
) -> dict[str, Any]:
    unique_generated_files = sorted(unique_strings(generated_files))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_analysis_path": analysis_path_label,
        "output_path": output_path_label,
        "generated_files": unique_generated_files,
        "generated_file_count": len(unique_generated_files),
        "module_count": module_count,
        "module_page_count": module_page_count,
        "file_page_count": file_page_count,
        "entity_count": entity_count,
        "dependency_count": dependency_count,
        "function_index_path": "functions/function-index.md",
        "file_index_path": "files/index.md",
        "documentation_layout_version": DOCUMENTATION_LAYOUT_VERSION,
        "warnings": sorted(unique_strings(warnings)),
        "renderer_version": RENDERER_VERSION,
    }


def cleanup_previous_generated_files(output_dir: Path) -> None:
    manifest_path = output_dir / "doc-manifest.json"
    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    if not isinstance(manifest, dict):
        return

    generated_files = manifest.get("generated_files")
    if not isinstance(generated_files, list):
        return

    for relative_name in generated_files:
        if not isinstance(relative_name, str):
            continue
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        target_path = output_dir / relative_path
        if target_path.is_file():
            target_path.unlink()
            remove_empty_parent_dirs(target_path.parent, output_dir)

    if manifest_path.is_file():
        manifest_path.unlink()


def remove_empty_parent_dirs(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def fallback_number(primary: Any, fallback: Any | None = None) -> Any:
    if primary is not None:
        return primary
    if fallback is not None:
        return fallback
    return NO_DATA
