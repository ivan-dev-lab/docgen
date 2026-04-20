from __future__ import annotations

import ast
import json
import os
import re
import sys
import tomllib
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__

SCHEMA_VERSION = "1.0"
TOOL_VERSION = __version__

IGNORED_DIRECTORY_NAMES = {
    ".docgen-analysis",
    ".docgen-analysis-live",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    ".git",
    "dist",
    "build",
    "out",
    "coverage",
    "htmlcov",
    ".next",
    ".turbo",
    "__pycache__",
    ".venv",
    "venv",
}
IGNORED_DIRECTORY_PREFIXES = {
    ".docgen-analysis",
    "docgen-smoke",
}
IGNORED_DIRECTORY_SUFFIXES = {
    ".egg-info",
}
GENERATED_ARTIFACT_FILENAMES = {
    "analysis-summary.json",
    "artifact-manifest.json",
    "coverage-report.json",
    "dependency-graph.json",
    "function-index.json",
    "inventory.json",
    "module-candidates.json",
}
GENERATED_ARCHIVE_PREFIX = ".docgen-analysis"
FIXTURE_PATH_MARKERS = (
    "tests/fixtures/",
    "testdata/",
    "fixtures/",
    "samples/",
    "sample_project/",
)

SOURCE_LANGUAGE_BY_EXTENSION = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".py": "python",
}

OTHER_LANGUAGE_BY_EXTENSION = {
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "config",
    ".conf": "config",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".html": "html",
    ".css": "css",
}

DOC_EXTENSIONS = {".md", ".rst", ".txt"}
ASSET_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".bmp",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pdf",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".bin",
    ".dat",
    ".zip",
}
CONFIG_EXTENSIONS = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf"}
CONFIG_FILENAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "tsconfig.json",
    "jsconfig.json",
    ".eslintrc",
    ".prettierrc",
    ".npmrc",
    ".gitignore",
}
PROJECT_MARKER_FILENAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "tsconfig.json",
    "jsconfig.json",
}
ENTRYPOINT_FILENAMES = {
    "index.js",
    "index.jsx",
    "index.ts",
    "index.tsx",
    "main.js",
    "main.ts",
    "main.py",
    "__main__.py",
    "app.py",
    "app.js",
    "app.ts",
    "server.js",
    "server.ts",
    "manage.py",
    "cli.py",
}
JS_RESOLUTION_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"]
SUPPORTED_DEEP_LANGUAGES = {"python", "javascript", "typescript"}
PYTHON_STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", set()))
NODE_BUILTIN_MODULES = {
    "assert",
    "assert/strict",
    "async_hooks",
    "buffer",
    "child_process",
    "cluster",
    "console",
    "constants",
    "crypto",
    "dgram",
    "diagnostics_channel",
    "dns",
    "dns/promises",
    "domain",
    "events",
    "fs",
    "fs/promises",
    "http",
    "http2",
    "https",
    "inspector",
    "module",
    "net",
    "os",
    "path",
    "path/posix",
    "path/win32",
    "perf_hooks",
    "process",
    "punycode",
    "querystring",
    "readline",
    "readline/promises",
    "repl",
    "stream",
    "stream/consumers",
    "stream/promises",
    "stream/web",
    "string_decoder",
    "sys",
    "timers",
    "timers/promises",
    "tls",
    "trace_events",
    "tty",
    "url",
    "util",
    "util/types",
    "v8",
    "vm",
    "wasi",
    "worker_threads",
    "zlib",
}
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
DEPENDENCY_TYPE_ORDER = {"internal": 0, "stdlib": 1, "node_builtin": 2, "third_party": 3, "unresolved": 4, "unknown": 5}
GENERIC_HEURISTIC_STEMS = {"__init__", "__main__", "index", "main", "app", "cli"}


def analyze_project(
    project_path: Path,
    output_dir: Path | None = None,
    *,
    include_tests: bool = True,
    include_fixtures: bool | None = None,
    include_generated: bool = False,
) -> Path:
    project_root = project_path.expanduser().resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"Project path does not exist: {project_path}")
    if not project_root.is_dir():
        raise NotADirectoryError(f"Project path is not a directory: {project_path}")

    output_root = output_dir.expanduser().resolve() if output_dir else project_root / ".docgen-analysis"
    output_root.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    root_is_fixture = is_fixture_path(str(project_root))
    effective_include_fixtures = include_fixtures if include_fixtures is not None else root_is_fixture

    inventory, inventory_limitations = collect_inventory(
        project_root,
        output_root,
        include_tests=include_tests,
        include_fixtures=effective_include_fixtures,
        include_generated=include_generated,
        root_is_fixture=root_is_fixture,
    )
    external_dependencies = collect_external_dependencies(project_root, inventory)
    third_party_index = build_third_party_index(external_dependencies)
    python_module_map, python_file_index = build_python_module_index(inventory)
    python_project_contexts = build_python_project_contexts(inventory)

    entities: list[dict[str, Any]] = []
    raw_imports: list[dict[str, Any]] = []
    deep_analyzed_files: set[str] = set()
    analysis_limitations = list(inventory_limitations)

    for record in inventory:
        if record["language"] == "python" and record["file_type"] in {"source", "test"}:
            file_entities, file_imports, file_limitations, analyzed_deeply = analyze_python_file(
                project_root,
                record,
                python_file_index,
            )
            entities.extend(file_entities)
            raw_imports.extend(file_imports)
            analysis_limitations.extend(file_limitations)
            if analyzed_deeply:
                deep_analyzed_files.add(record["path"])
        elif record["language"] in {"javascript", "typescript"} and record["file_type"] in {"source", "test"}:
            file_entities, file_imports, file_limitations, analyzed_deeply = analyze_javascript_file(project_root, record)
            entities.extend(file_entities)
            raw_imports.extend(file_imports)
            analysis_limitations.extend(file_limitations)
            if analyzed_deeply:
                deep_analyzed_files.add(record["path"])

    annotate_inventory_analysis(inventory, deep_analyzed_files)

    dependency_graph = build_dependency_graph(
        project_root=project_root,
        generated_at=generated_at,
        inventory=inventory,
        raw_imports=raw_imports,
        python_module_map=python_module_map,
        python_file_index=python_file_index,
        python_project_contexts=python_project_contexts,
        external_dependencies=external_dependencies,
        third_party_index=third_party_index,
    )
    test_relations = build_test_relations(inventory, dependency_graph["imports"])
    module_candidates = build_module_candidates(
        generated_at=generated_at,
        project_root=project_root,
        inventory=inventory,
        imports=dependency_graph["imports"],
        test_relations=test_relations,
    )
    coverage_report = build_coverage_report(
        generated_at=generated_at,
        project_root=project_root,
        inventory=inventory,
        entities=entities,
        imports=dependency_graph["imports"],
        deep_analyzed_files=deep_analyzed_files,
        limitations=analysis_limitations,
    )
    summary = build_analysis_summary(
        generated_at=generated_at,
        project_root=project_root,
        output_root=output_root,
        inventory=inventory,
        entities=entities,
        imports=dependency_graph["imports"],
        limitations=analysis_limitations,
        coverage_report=coverage_report,
    )

    artifacts: dict[str, dict[str, Any]] = {
        "inventory.json": artifact_payload(
            generated_at=generated_at,
            project_root=project_root,
            files=inventory,
        ),
        "function-index.json": artifact_payload(
            generated_at=generated_at,
            project_root=project_root,
            entities=sort_entities(entities),
        ),
        "dependency-graph.json": dependency_graph,
        "module-candidates.json": module_candidates,
        "analysis-summary.json": summary,
        "coverage-report.json": coverage_report,
    }

    artifact_manifest = build_artifact_manifest(
        generated_at=generated_at,
        project_root=project_root,
        output_root=output_root,
        artifacts=artifacts,
    )
    artifacts["artifact-manifest.json"] = artifact_manifest

    for filename, payload in artifacts.items():
        write_json(output_root / filename, payload)
    return output_root


def artifact_payload(generated_at: str, project_root: Path, **payload: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_path": str(project_root),
        "tool_version": TOOL_VERSION,
        **payload,
    }


def collect_inventory(
    project_root: Path,
    output_root: Path,
    *,
    include_tests: bool,
    include_fixtures: bool,
    include_generated: bool,
    root_is_fixture: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    files: list[dict[str, Any]] = []
    ignored_names = {name.casefold() for name in IGNORED_DIRECTORY_NAMES}
    limitations: list[str] = []

    for current_root_str, dirnames, filenames in os.walk(project_root, topdown=True):
        current_root = Path(current_root_str)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not should_ignore_directory_name(dirname, current_root / dirname, output_root, ignored_names)
            and (current_root / dirname).resolve() != output_root
        ]

        for filename in filenames:
            absolute_path = current_root / filename
            relative_path = to_posix_path(absolute_path.relative_to(project_root))
            is_fixture = root_is_fixture or is_fixture_path(relative_path)
            is_generated = is_generated_path(relative_path)
            is_packaging_metadata = is_packaging_metadata_path(relative_path)

            if is_packaging_metadata and not include_generated:
                continue
            if is_generated and not include_generated:
                continue
            if is_fixture and not include_fixtures:
                continue

            extension = absolute_path.suffix.lower()
            language = detect_language(absolute_path.name, extension)
            is_test = is_test_file(relative_path)
            if is_test and not include_tests:
                continue
            is_config = is_config_file(relative_path, extension)
            is_entrypoint = is_possible_entrypoint(relative_path, extension)
            file_type = classify_file(relative_path, extension, is_test, is_config)
            supports_deep_analysis = file_type in {"source", "test"} and language in SUPPORTED_DEEP_LANGUAGES
            artifact_role = determine_artifact_role(
                is_fixture=is_fixture,
                is_generated=is_generated,
                is_packaging_metadata=is_packaging_metadata,
            )

            files.append(
                {
                    "path": relative_path,
                    "extension": extension,
                    "file_type": file_type,
                    "size_bytes": absolute_path.stat().st_size,
                    "language": language,
                    "is_test": is_test,
                    "is_config": is_config,
                    "is_possible_entrypoint": is_entrypoint,
                    "supports_deep_analysis": supports_deep_analysis,
                    "is_fixture": is_fixture,
                    "is_generated": is_generated,
                    "is_packaging_metadata": is_packaging_metadata,
                    "artifact_role": artifact_role,
                }
            )

    files.sort(key=lambda item: item["path"])
    if include_generated:
        limitations.append("Generated outputs and packaging metadata were explicitly included in inventory.")
    if include_fixtures:
        limitations.append("Fixture/sample files were included in inventory.")
    if not include_tests:
        limitations.append("Test files were excluded from inventory by CLI option.")
    return files, limitations


def annotate_inventory_analysis(inventory: list[dict[str, Any]], deep_analyzed_files: set[str]) -> None:
    for record in inventory:
        if record["path"] in deep_analyzed_files:
            record["analysis_depth"] = "deep"
        elif record["supports_deep_analysis"]:
            record["analysis_depth"] = "partial"
        else:
            record["analysis_depth"] = "shallow"


def should_ignore_directory_name(
    dirname: str,
    absolute_path: Path,
    output_root: Path,
    ignored_names: set[str],
) -> bool:
    normalized = dirname.casefold()
    if absolute_path.resolve() == output_root:
        return True
    if normalized in ignored_names:
        return True
    if any(normalized.startswith(prefix.casefold()) for prefix in IGNORED_DIRECTORY_PREFIXES):
        return True
    if any(normalized.endswith(suffix.casefold()) for suffix in IGNORED_DIRECTORY_SUFFIXES):
        return True
    return False


def is_fixture_path(path_text: str) -> bool:
    normalized = normalize_path_text(path_text)
    return any(marker in normalized for marker in FIXTURE_PATH_MARKERS)


def is_generated_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    filename = path.name.casefold()
    normalized = normalize_path_text(relative_path)
    return (
        filename in GENERATED_ARTIFACT_FILENAMES
        or (filename.startswith(GENERATED_ARCHIVE_PREFIX) and filename.endswith(".zip"))
        or any(part.casefold().startswith(".docgen-analysis") for part in path.parts)
        or "docgen-smoke/" in normalized
    )


def is_packaging_metadata_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    return any(part.casefold().endswith(".egg-info") for part in path.parts)


def determine_artifact_role(
    *,
    is_fixture: bool,
    is_generated: bool,
    is_packaging_metadata: bool,
) -> str | None:
    if is_packaging_metadata:
        return "packaging_metadata"
    if is_generated:
        return "generated"
    if is_fixture:
        return "fixture"
    return None


def detect_language(filename: str, extension: str) -> str:
    if extension in SOURCE_LANGUAGE_BY_EXTENSION:
        return SOURCE_LANGUAGE_BY_EXTENSION[extension]
    if extension in OTHER_LANGUAGE_BY_EXTENSION:
        return OTHER_LANGUAGE_BY_EXTENSION[extension]
    if filename in {"Dockerfile", "Makefile"}:
        return filename.lower()
    return "unknown"


def classify_file(relative_path: str, extension: str, is_test: bool, is_config: bool) -> str:
    path = PurePosixPath(relative_path)
    lower_parts = [part.casefold() for part in path.parts]

    if is_test:
        return "test"
    if is_config:
        return "config"
    if extension in SOURCE_LANGUAGE_BY_EXTENSION:
        return "source"
    if extension in DOC_EXTENSIONS or "docs" in lower_parts:
        return "doc"
    if extension in ASSET_EXTENSIONS or any(part in {"assets", "static", "public"} for part in lower_parts[:-1]):
        return "asset"
    return "unknown"


def is_test_file(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    lower_parts = [part.casefold() for part in path.parts]
    filename = path.name.casefold()
    stem = path.stem.casefold()
    has_fixture_marker = any(part in {"fixtures", "fixture"} for part in lower_parts)
    in_test_directory = any(part in {"tests", "__tests__", "spec"} for part in lower_parts)
    return (
        (in_test_directory and not has_fixture_marker)
        or filename.startswith("test_")
        or stem.endswith("_test")
        or ".test." in filename
        or ".spec." in filename
    )


def is_config_file(relative_path: str, extension: str) -> bool:
    path = PurePosixPath(relative_path)
    filename = path.name.casefold()
    lower_parts = [part.casefold() for part in path.parts[:-1]]

    if filename in CONFIG_FILENAMES:
        return True
    if filename.startswith(".env"):
        return True
    if any(filename.endswith(suffix) for suffix in (".config.js", ".config.cjs", ".config.mjs", ".config.ts")):
        return True
    if "config" in lower_parts and extension in CONFIG_EXTENSIONS:
        return True
    if filename.endswith(".yaml") or filename.endswith(".yml"):
        return "config" in lower_parts
    return False


def is_possible_entrypoint(relative_path: str, extension: str) -> bool:
    path = PurePosixPath(relative_path)
    filename = path.name.casefold()
    lower_parts = [part.casefold() for part in path.parts]

    if filename in ENTRYPOINT_FILENAMES:
        if len(lower_parts) == 1:
            return True
        if lower_parts[0] in {"src", "app", "bin", "scripts"}:
            return True
        if filename in {"__main__.py", "manage.py", "cli.py"}:
            return True

    return (
        extension in SOURCE_LANGUAGE_BY_EXTENSION
        and len(lower_parts) >= 2
        and lower_parts[0] in {"bin", "scripts"}
    )


def analyze_python_file(
    project_root: Path,
    record: dict[str, Any],
    python_file_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], bool]:
    absolute_path = project_root / record["path"]
    source = read_text_file(absolute_path)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], [], [f"Python parser skipped {record['path']}: {exc.msg} at line {exc.lineno}."], False

    exported_names = extract_python_all(tree)
    module_name = python_file_index.get(record["path"], {}).get("module_name")
    collector = PythonCollector(
        file_path=record["path"],
        exported_names=exported_names,
        module_name=module_name,
        source=source,
    )
    collector.visit(tree)
    return collector.entities, collector.imports, [], True


class PythonCollector(ast.NodeVisitor):
    def __init__(self, file_path: str, exported_names: set[str], module_name: str | None, source: str) -> None:
        self.file_path = file_path
        self.exported_names = exported_names
        self.module_name = module_name
        self.source = source
        self.entities: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.class_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            self.imports.append(
                {
                    "source_file": self.file_path,
                    "language": "python",
                    "import_kind": "import",
                    "line_start": node.lineno,
                    "imported": alias.name,
                    "raw": ast.get_source_segment(self.source, node),
                }
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        self.imports.append(
            {
                "source_file": self.file_path,
                "language": "python",
                "import_kind": "from",
                "line_start": node.lineno,
                "imported": "." * node.level + (node.module or ""),
                "module": node.module,
                "level": node.level,
                "names": [alias.name for alias in node.names],
                "raw": ast.get_source_segment(self.source, node),
            }
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._append_function(node, is_async=False)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._append_function(node, is_async=True)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        exported = self._resolve_exported(node.name, top_level=not self.class_stack)
        bases = [safe_unparse(base) for base in node.bases if safe_unparse(base)]
        docstring = ast.get_docstring(node)
        signature = f"class {node.name}"
        if bases:
            signature = f"{signature}({', '.join(bases)})"

        self.entities.append(
            {
                "name": node.name,
                "file": self.file_path,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "entity_type": "class",
                "type": "class",
                "exported": exported,
                "context": self.class_stack[-1] if self.class_stack else None,
                "parent": self.class_stack[-1] if self.class_stack else None,
                "container": self.class_stack[-1] if self.class_stack else self.module_name,
                "language": "python",
                "signature": signature,
                "parameters": [],
                "return_annotation": None,
                "docstring": docstring,
                "decorators": [safe_unparse(decorator) for decorator in node.decorator_list if safe_unparse(decorator)],
                "is_async": False,
                "confidence": "high",
            }
        )
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _append_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        is_method = bool(self.class_stack)
        exported = None if is_method else self._resolve_exported(node.name, top_level=True)
        entity_type = "method" if is_method else "function"
        parameters = build_python_parameters(node.args)
        return_annotation = safe_unparse(node.returns)
        signature = build_python_signature(node.name, parameters, return_annotation, is_async)

        self.entities.append(
            {
                "name": node.name,
                "file": self.file_path,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "entity_type": entity_type,
                "type": entity_type,
                "exported": exported,
                "context": self.class_stack[-1] if self.class_stack else None,
                "parent": self.class_stack[-1] if self.class_stack else None,
                "container": self.class_stack[-1] if self.class_stack else self.module_name,
                "language": "python",
                "signature": signature,
                "parameters": parameters,
                "return_annotation": return_annotation,
                "docstring": ast.get_docstring(node),
                "decorators": [safe_unparse(decorator) for decorator in node.decorator_list if safe_unparse(decorator)],
                "is_async": is_async,
                "confidence": "high",
            }
        )

    def _resolve_exported(self, name: str, top_level: bool) -> bool | None:
        if not top_level:
            return None
        if name in self.exported_names:
            return True
        if name.startswith("_"):
            return False
        return None


def build_python_parameters(arguments: ast.arguments) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults = [None] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)

    for index, argument in enumerate(arguments.posonlyargs):
        parameters.append(python_parameter(argument, "positional_only", defaults[index]))

    offset = len(arguments.posonlyargs)
    for index, argument in enumerate(arguments.args):
        parameters.append(python_parameter(argument, "positional_or_keyword", defaults[offset + index]))

    if arguments.vararg:
        parameters.append(python_parameter(arguments.vararg, "var_positional", None))

    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        parameters.append(python_parameter(argument, "keyword_only", default))

    if arguments.kwarg:
        parameters.append(python_parameter(arguments.kwarg, "var_keyword", None))

    return parameters


def python_parameter(argument: ast.arg, kind: str, default: ast.AST | None) -> dict[str, Any]:
    return {
        "name": argument.arg,
        "kind": kind,
        "annotation": safe_unparse(argument.annotation),
        "default": safe_unparse(default),
    }


def build_python_signature(
    name: str,
    parameters: list[dict[str, Any]],
    return_annotation: str | None,
    is_async: bool,
) -> str:
    rendered_parameters = []
    saw_positional_only = False
    saw_var_positional = False

    for parameter in parameters:
        rendered = render_python_parameter(parameter)
        rendered_parameters.append(rendered)
        if parameter["kind"] == "positional_only":
            saw_positional_only = True
        elif saw_positional_only and parameter["kind"] != "positional_only":
            rendered_parameters.insert(len(rendered_parameters) - 1, "/")
            saw_positional_only = False
        if parameter["kind"] == "var_positional":
            saw_var_positional = True

    if saw_positional_only:
        rendered_parameters.append("/")

    has_keyword_only = any(parameter["kind"] == "keyword_only" for parameter in parameters)
    if has_keyword_only and not saw_var_positional:
        insert_at = next(
            (index for index, parameter in enumerate(parameters) if parameter["kind"] == "keyword_only"),
            len(parameters),
        )
        rendered_parameters.insert(insert_at, "*")

    prefix = "async def" if is_async else "def"
    signature = f"{prefix} {name}({', '.join(rendered_parameters)})"
    if return_annotation:
        signature = f"{signature} -> {return_annotation}"
    return signature


def render_python_parameter(parameter: dict[str, Any]) -> str:
    prefix = ""
    if parameter["kind"] == "var_positional":
        prefix = "*"
    elif parameter["kind"] == "var_keyword":
        prefix = "**"

    rendered = f"{prefix}{parameter['name']}"
    if parameter.get("annotation"):
        rendered = f"{rendered}: {parameter['annotation']}"
    if parameter.get("default"):
        rendered = f"{rendered} = {parameter['default']}"
    return rendered


def extract_python_all(tree: ast.AST) -> set[str]:
    exported: set[str] = set()
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                    for element in node.value.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            exported.add(element.value)
    return exported


def analyze_javascript_file(project_root: Path, record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], bool]:
    absolute_path = project_root / record["path"]
    source = read_text_file(absolute_path)
    lines = source.splitlines()
    exported_names = collect_javascript_exports(lines)
    entities = collect_javascript_entities(record["path"], record["language"], lines, exported_names)
    imports = collect_javascript_imports(record["path"], record["language"], lines)
    return entities, imports, [], True


def collect_javascript_exports(lines: list[str]) -> set[str]:
    exported: set[str] = set()
    export_list_pattern = re.compile(r"^\s*export\s*{\s*(.+?)\s*}\s*;?\s*$")

    for line in lines:
        stripped = line.strip()
        for pattern in (
            r"^export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
            r"^export\s+(?:default\s+)?class\s+([A-Za-z_$][\w$]*)",
            r"^export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
            r"^export\s+default\s+([A-Za-z_$][\w$]*)\s*;?\s*$",
        ):
            match = re.match(pattern, stripped)
            if match:
                exported.add(match.group(1))

        list_match = export_list_pattern.match(stripped)
        if list_match:
            for item in list_match.group(1).split(","):
                left_side = item.split(" as ", 1)[0].strip()
                if left_side:
                    exported.add(left_side)

    return exported


def collect_javascript_entities(
    file_path: str,
    language: str,
    lines: list[str],
    exported_names: set[str],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    class_ranges: list[tuple[int, int, str]] = []
    top_level_depth = 0

    class_pattern = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+([^{]+))?"
    )
    function_pattern = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:(async)\s+)?function\s+([A-Za-z_$][\w$]*)\s*\((.*?)\)\s*(?::\s*([^{]+))?\s*\{"
    )
    arrow_pattern = re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:(async)\s*)?(?:(\((.*?)\))|([A-Za-z_$][\w$]*))\s*(?::\s*([^=]+))?=>"
    )

    for index, line in enumerate(lines):
        current_depth = top_level_depth

        class_match = class_pattern.match(line)
        if class_match and current_depth == 0:
            class_name = class_match.group(1)
            extends_clause = class_match.group(2).strip() if class_match.group(2) else None
            line_end = estimate_javascript_block_end(lines, index)
            class_ranges.append((index, max(line_end - 1, index), class_name))
            signature = f"class {class_name}"
            if extends_clause:
                signature = f"{signature} extends {extends_clause}"
            entities.append(
                javascript_entity(
                    name=class_name,
                    file_path=file_path,
                    line_start=index + 1,
                    line_end=line_end,
                    entity_type="class",
                    exported=class_name in exported_names,
                    language=language,
                    signature=signature,
                    parameters=[],
                    return_annotation=None,
                    docstring=extract_leading_jsdoc(lines, index),
                    parent=None,
                    container=None,
                    is_async=False,
                    confidence="medium",
                )
            )

        function_match = function_pattern.match(line)
        if function_match and current_depth == 0:
            function_name = function_match.group(2)
            raw_parameters = function_match.group(3)
            return_annotation = normalize_inline_text(function_match.group(4))
            parameters, parameter_confidence = parse_javascript_parameters(raw_parameters)
            line_end = estimate_javascript_block_end(lines, index)
            entities.append(
                javascript_entity(
                    name=function_name,
                    file_path=file_path,
                    line_start=index + 1,
                    line_end=line_end,
                    entity_type="function",
                    exported=function_name in exported_names,
                    language=language,
                    signature=collect_javascript_signature(lines, index),
                    parameters=parameters,
                    return_annotation=return_annotation,
                    docstring=extract_leading_jsdoc(lines, index),
                    parent=None,
                    container=None,
                    is_async=bool(function_match.group(1)),
                    confidence="medium" if parameter_confidence != "low" else "low",
                )
            )

        arrow_match = arrow_pattern.match(line)
        if arrow_match and current_depth == 0:
            function_name = arrow_match.group(1)
            is_async = bool(arrow_match.group(2))
            raw_parameters = arrow_match.group(4) if arrow_match.group(3) else arrow_match.group(5)
            return_annotation = normalize_inline_text(arrow_match.group(6))
            parameters, parameter_confidence = parse_javascript_parameters(raw_parameters)
            line_end = estimate_javascript_block_end(lines, index)
            entities.append(
                javascript_entity(
                    name=function_name,
                    file_path=file_path,
                    line_start=index + 1,
                    line_end=line_end,
                    entity_type="function",
                    exported=function_name in exported_names,
                    language=language,
                    signature=collect_javascript_signature(lines, index),
                    parameters=parameters,
                    return_annotation=return_annotation,
                    docstring=extract_leading_jsdoc(lines, index),
                    parent=None,
                    container=None,
                    is_async=is_async,
                    confidence="medium" if parameter_confidence != "low" else "low",
                )
            )

        top_level_depth += brace_delta(line)

    method_pattern = re.compile(
        r"^\s*(?:static\s+)?(?:(async)\s+)?([A-Za-z_$][\w$]*)\s*\((.*?)\)\s*(?::\s*([^{]+))?\{"
    )
    skipped_method_names = {"if", "for", "while", "switch", "catch", "function"}

    for class_start, class_end, class_name in class_ranges:
        class_depth = 0
        class_started = False
        for index in range(class_start, min(class_end + 1, len(lines))):
            line = lines[index]
            current_depth = class_depth
            if class_started and current_depth == 1:
                method_match = method_pattern.match(line)
                if method_match:
                    method_name = method_match.group(2)
                    if method_name not in skipped_method_names:
                        parameters, parameter_confidence = parse_javascript_parameters(method_match.group(3))
                        entities.append(
                            javascript_entity(
                                name=method_name,
                                file_path=file_path,
                                line_start=index + 1,
                                line_end=min(estimate_javascript_block_end(lines, index), class_end + 1),
                                entity_type="method",
                                exported=None,
                                language=language,
                                signature=collect_javascript_signature(lines, index),
                                parameters=parameters,
                                return_annotation=normalize_inline_text(method_match.group(4)),
                                docstring=extract_leading_jsdoc(lines, index),
                                parent=class_name,
                                container=class_name,
                                is_async=bool(method_match.group(1)),
                                confidence="medium" if parameter_confidence != "low" else "low",
                            )
                        )

            class_depth += brace_delta(line)
            if class_depth > 0:
                class_started = True

    return sort_entities(deduplicate_entities(entities))


def javascript_entity(
    name: str,
    file_path: str,
    line_start: int,
    line_end: int,
    entity_type: str,
    exported: bool | None,
    language: str,
    signature: str | None,
    parameters: list[dict[str, Any]],
    return_annotation: str | None,
    docstring: str | None,
    parent: str | None,
    container: str | None,
    is_async: bool,
    confidence: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "file": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "entity_type": entity_type,
        "type": entity_type,
        "exported": exported,
        "context": parent,
        "parent": parent,
        "container": container,
        "language": language,
        "signature": signature,
        "parameters": parameters,
        "return_annotation": return_annotation,
        "docstring": docstring,
        "decorators": [],
        "is_async": is_async,
        "confidence": confidence,
    }


def collect_javascript_imports(file_path: str, language: str, lines: list[str]) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []

    import_from_pattern = re.compile(r"^\s*import\s+(?:type\s+)?[^'\"]*?\s+from\s+['\"]([^'\"]+)['\"]")
    side_effect_pattern = re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]")
    export_from_pattern = re.compile(r"^\s*export\s+.+?\s+from\s+['\"]([^'\"]+)['\"]")
    require_pattern = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")

    for line_number, line in enumerate(lines, start=1):
        for pattern, kind in (
            (import_from_pattern, "import"),
            (side_effect_pattern, "import"),
            (export_from_pattern, "re-export"),
        ):
            match = pattern.search(line)
            if match:
                imports.append(
                    {
                        "source_file": file_path,
                        "language": language,
                        "import_kind": kind,
                        "line_start": line_number,
                        "imported": match.group(1),
                    }
                )

        for match in require_pattern.finditer(line):
            imports.append(
                {
                    "source_file": file_path,
                    "language": language,
                    "import_kind": "require",
                    "line_start": line_number,
                    "imported": match.group(1),
                }
            )

    return imports


def collect_javascript_signature(lines: list[str], start_index: int) -> str | None:
    chunks: list[str] = []
    for offset in range(0, 8):
        index = start_index + offset
        if index >= len(lines):
            break
        stripped = lines[index].strip()
        if not stripped:
            break
        chunks.append(stripped)
        if "=>" in stripped or "{" in stripped:
            break

    if not chunks:
        return None

    signature = normalize_inline_text(" ".join(chunks))
    if signature and signature.endswith("{"):
        signature = signature[:-1].rstrip()
    return signature


def extract_leading_jsdoc(lines: list[str], line_index: int) -> str | None:
    index = line_index - 1
    while index >= 0 and not lines[index].strip():
        index -= 1

    if index < 0 or "*/" not in lines[index]:
        return None

    comment_lines = [lines[index].strip()]
    index -= 1
    while index >= 0:
        stripped = lines[index].strip()
        comment_lines.append(stripped)
        if stripped.startswith("/**"):
            return clean_jsdoc(list(reversed(comment_lines)))
        if stripped.startswith("/*") or stripped.startswith("//"):
            return None
        index -= 1
    return None


def clean_jsdoc(lines: list[str]) -> str | None:
    cleaned = []
    for line in lines:
        text = line
        text = text.removeprefix("/**").removeprefix("/*")
        text = text.removesuffix("*/")
        text = text.lstrip("*").strip()
        if text:
            cleaned.append(text)
    return "\n".join(cleaned) if cleaned else None


def parse_javascript_parameters(raw_parameters: str | None) -> tuple[list[dict[str, Any]], str]:
    if raw_parameters is None:
        return [], "medium"

    raw_parameters = raw_parameters.strip()
    if not raw_parameters:
        return [], "medium"

    parameters: list[dict[str, Any]] = []
    confidence = "medium"

    for token in split_top_level_commas(raw_parameters):
        parameter, parameter_confidence = parse_javascript_parameter(token)
        parameters.append(parameter)
        if parameter_confidence == "low":
            confidence = "low"

    return parameters, confidence


def parse_javascript_parameter(token: str) -> tuple[dict[str, Any], str]:
    raw = token.strip()
    if not raw:
        return {"name": "", "kind": "unknown", "annotation": None, "default": None, "raw": raw}, "low"

    rest = raw.startswith("...")
    if rest:
        raw = raw[3:].strip()

    name_part, default_part = split_top_level_once(raw, "=")
    name_part = name_part.strip()
    annotation = None
    confidence = "medium"

    if name_part.startswith("{") or name_part.startswith("["):
        name = name_part
        confidence = "low"
    else:
        left, right = split_top_level_once(name_part, ":")
        if right is not None:
            name = left.strip()
            annotation = right.strip() or None
        else:
            name = name_part

    optional = name.endswith("?")
    if optional:
        name = name[:-1]

    parameter = {
        "name": name or name_part,
        "kind": "rest" if rest else "parameter",
        "annotation": annotation,
        "default": default_part.strip() if default_part else None,
        "optional": optional,
        "raw": token.strip(),
    }
    return parameter, confidence


def split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    in_string: str | None = None
    escaping = False

    for character in text:
        current.append(character)
        if in_string:
            if escaping:
                escaping = False
            elif character == "\\":
                escaping = True
            elif character == in_string:
                in_string = None
            continue

        if character in {"'", '"', "`"}:
            in_string = character
            continue
        if character in "([{<":
            stack.append(character)
            continue
        if character in ")]}>":
            if stack:
                stack.pop()
            continue
        if character == "," and not stack:
            current.pop()
            parts.append("".join(current).strip())
            current = []

    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def split_top_level_once(text: str, delimiter: str) -> tuple[str, str | None]:
    stack: list[str] = []
    in_string: str | None = None
    escaping = False

    for index, character in enumerate(text):
        if in_string:
            if escaping:
                escaping = False
            elif character == "\\":
                escaping = True
            elif character == in_string:
                in_string = None
            continue

        if character in {"'", '"', "`"}:
            in_string = character
            continue
        if character in "([{<":
            stack.append(character)
            continue
        if character in ")]}>":
            if stack:
                stack.pop()
            continue
        if character == delimiter and not stack:
            return text[:index], text[index + 1 :]
    return text, None


def estimate_javascript_block_end(lines: list[str], start_index: int) -> int:
    block_started = False
    depth = 0
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not block_started and "{" not in line:
            if "=>" in line and ";" in line:
                return index + 1
            continue
        if "{" in line:
            block_started = True
        if block_started:
            depth += brace_delta(line)
            if depth <= 0:
                return index + 1
    return start_index + 1


def brace_delta(line: str) -> int:
    cleaned = re.sub(r"//.*$", "", line)
    return cleaned.count("{") - cleaned.count("}")


def build_python_module_index(
    inventory: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    module_to_file: dict[str, str] = {}
    file_to_module: dict[str, dict[str, Any]] = {}

    for record in inventory:
        if record["language"] != "python":
            continue

        relative = PurePosixPath(record["path"])
        parts = list(relative.with_suffix("").parts)
        is_package_init = bool(parts and parts[-1] == "__init__")
        if is_package_init:
            module_name = ".".join(parts[:-1])
            package_parts = parts[:-1]
        else:
            module_name = ".".join(parts)
            package_parts = parts[:-1]

        file_to_module[record["path"]] = {
            "module_name": module_name,
            "package_parts": package_parts,
            "is_package_init": is_package_init,
        }
        if module_name:
            module_to_file[module_name] = record["path"]

    return module_to_file, file_to_module


def build_python_project_contexts(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    project_roots = {"."}
    for record in inventory:
        if PurePosixPath(record["path"]).name in PROJECT_MARKER_FILENAMES:
            parent = str(PurePosixPath(record["path"]).parent)
            project_roots.add(parent)

    sorted_roots = sorted(project_roots, key=path_depth, reverse=True)
    module_maps: dict[str, dict[str, str]] = {}
    root_modules: dict[str, set[str]] = {}
    file_context_roots: dict[str, list[str]] = {}

    for root in sorted_roots:
        module_map: dict[str, str] = {}
        for record in inventory:
            if record["language"] != "python":
                continue
            if not path_in_directory(record["path"], root):
                continue
            relative_path = relative_within(record["path"], root)
            module_name = python_module_name_from_relative_path(relative_path)
            if module_name:
                module_map[module_name] = record["path"]
        module_maps[root] = module_map
        root_modules[root] = {module.split(".", 1)[0] for module in module_map}

    for record in inventory:
        if record["language"] != "python":
            continue
        file_context_roots[record["path"]] = [root for root in sorted_roots if path_in_directory(record["path"], root)]

    return {
        "module_maps": module_maps,
        "root_modules": root_modules,
        "file_context_roots": file_context_roots,
    }


def build_dependency_graph(
    project_root: Path,
    generated_at: str,
    inventory: list[dict[str, Any]],
    raw_imports: list[dict[str, Any]],
    python_module_map: dict[str, str],
    python_file_index: dict[str, dict[str, Any]],
    python_project_contexts: dict[str, Any],
    external_dependencies: list[dict[str, Any]],
    third_party_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    local_python_roots = {module.split(".", 1)[0] for module in python_module_map}
    imports: list[dict[str, Any]] = []

    for raw_import in raw_imports:
        if raw_import["language"] == "python":
            imports.extend(
                resolve_python_import(
                    raw_import=raw_import,
                    python_module_map=python_module_map,
                    python_file_index=python_file_index,
                    python_project_contexts=python_project_contexts,
                    local_python_roots=local_python_roots,
                    third_party_index=third_party_index,
                )
            )
        else:
            imports.append(resolve_javascript_import(project_root, raw_import, third_party_index))

    imports.sort(key=lambda item: (item["source_file"], item["line_start"], item["imported"]))
    dependency_type_counts = dict(sorted(count_values(edge["dependency_type"] for edge in imports).items()))

    return artifact_payload(
        generated_at=generated_at,
        project_root=project_root,
        imports=imports,
        dependency_type_counts=dependency_type_counts,
        external_dependencies=external_dependencies,
    )


def resolve_python_import(
    raw_import: dict[str, Any],
    python_module_map: dict[str, str],
    python_file_index: dict[str, dict[str, Any]],
    python_project_contexts: dict[str, Any],
    local_python_roots: set[str],
    third_party_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    source_file = raw_import["source_file"]
    file_context = python_file_index.get(source_file, {"module_name": "", "package_parts": [], "is_package_init": False})
    context_roots = python_project_contexts["file_context_roots"].get(source_file, [])
    source_local_roots = set(local_python_roots)
    for root in context_roots:
        source_local_roots.update(python_project_contexts["root_modules"].get(root, set()))
    edges: list[dict[str, Any]] = []

    if raw_import["import_kind"] == "import":
        imported = raw_import["imported"]
        resolved_file = resolve_python_module(imported, python_module_map)
        if not resolved_file:
            resolved_file = resolve_python_module_in_contexts(imported, context_roots, python_project_contexts["module_maps"])
        dependency_type, matched_dependency = classify_python_dependency(
            imported=imported,
            resolved_file=resolved_file,
            is_relative=False,
            local_python_roots=source_local_roots,
            third_party_index=third_party_index,
        )
        edges.append(
            build_import_edge(
                source_file=source_file,
                imported=imported,
                line_start=raw_import["line_start"],
                import_kind="import",
                language="python",
                resolved_file=resolved_file,
                dependency_type=dependency_type,
                matched_dependency=matched_dependency,
            )
        )
        return edges

    base_module = resolve_python_from_base(raw_import, file_context)
    names = raw_import.get("names", [])
    is_relative = bool(raw_import.get("level"))

    candidate_modules = []
    for name in names:
        if base_module:
            candidate = f"{base_module}.{name}"
            if candidate in python_module_map or resolve_python_module_in_contexts(
                candidate,
                context_roots,
                python_project_contexts["module_maps"],
            ):
                candidate_modules.append(candidate)

    if candidate_modules:
        for candidate in candidate_modules:
            resolved_file = resolve_python_module(candidate, python_module_map)
            if not resolved_file:
                resolved_file = resolve_python_module_in_contexts(candidate, context_roots, python_project_contexts["module_maps"])
            edges.append(
                build_import_edge(
                    source_file=source_file,
                    imported=candidate,
                    line_start=raw_import["line_start"],
                    import_kind="from",
                    language="python",
                    resolved_file=resolved_file,
                    dependency_type="internal",
                    matched_dependency=None,
                )
            )
        return edges

    resolved_file = resolve_python_module(base_module, python_module_map) if base_module else None
    if not resolved_file and base_module:
        resolved_file = resolve_python_module_in_contexts(base_module, context_roots, python_project_contexts["module_maps"])
    imported_label = base_module or raw_import["imported"] or "." * raw_import.get("level", 0)
    dependency_type, matched_dependency = classify_python_dependency(
        imported=imported_label,
        resolved_file=resolved_file,
        is_relative=is_relative,
        local_python_roots=source_local_roots,
        third_party_index=third_party_index,
    )
    edges.append(
        build_import_edge(
            source_file=source_file,
            imported=imported_label,
            line_start=raw_import["line_start"],
            import_kind="from",
            language="python",
            resolved_file=resolved_file,
            dependency_type=dependency_type,
            matched_dependency=matched_dependency,
        )
    )
    return edges


def classify_python_dependency(
    imported: str,
    resolved_file: str | None,
    is_relative: bool,
    local_python_roots: set[str],
    third_party_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> tuple[str, list[dict[str, Any]] | None]:
    if resolved_file:
        return "internal", None

    root = imported.lstrip(".").split(".", 1)[0] if imported else ""
    if is_relative:
        return "internal", None
    if root and root in local_python_roots:
        return "internal", None
    if root and root in PYTHON_STDLIB_MODULES:
        return "stdlib", None

    normalized = normalize_python_dependency_name(root)
    matches = third_party_index["python"].get(normalized)
    if matches:
        return "third_party", matches
    if imported:
        return "unresolved", None
    return "unknown", None


def resolve_python_from_base(raw_import: dict[str, Any], file_context: dict[str, Any]) -> str:
    level = raw_import.get("level", 0) or 0
    module = raw_import.get("module") or ""
    package_parts = list(file_context.get("package_parts", []))

    if level == 0:
        return module

    keep_count = max(len(package_parts) - (level - 1), 0)
    anchor = package_parts[:keep_count]
    if module:
        return ".".join([*anchor, *module.split(".")])
    return ".".join(anchor)


def resolve_python_module(module_name: str | None, python_module_map: dict[str, str]) -> str | None:
    if not module_name:
        return None
    return python_module_map.get(module_name)


def resolve_python_module_in_contexts(
    module_name: str,
    context_roots: list[str],
    module_maps: dict[str, dict[str, str]],
) -> str | None:
    for root in context_roots:
        resolved_file = module_maps.get(root, {}).get(module_name)
        if resolved_file:
            return resolved_file
    return None


def resolve_javascript_import(
    project_root: Path,
    raw_import: dict[str, Any],
    third_party_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    specifier = raw_import["imported"]
    source_file = raw_import["source_file"]
    source_path = project_root / PurePosixPath(source_file)
    resolved_file = None

    if specifier.startswith(".") or specifier.startswith("/"):
        base_path = project_root / specifier.lstrip("/") if specifier.startswith("/") else source_path.parent / specifier
        resolved_file = resolve_javascript_path(project_root, base_path)
        dependency_type = "internal"
        matched_dependency = None
    else:
        builtin_name = strip_node_prefix(specifier)
        manifest_key = normalize_node_dependency_name(specifier)
        matches = third_party_index["node"].get(manifest_key)
        if builtin_name in NODE_BUILTIN_MODULES:
            dependency_type = "node_builtin"
            matched_dependency = None
        elif matches:
            dependency_type = "third_party"
            matched_dependency = matches
        elif specifier:
            dependency_type = "unresolved"
            matched_dependency = None
        else:
            dependency_type = "unknown"
            matched_dependency = None

    return build_import_edge(
        source_file=source_file,
        imported=specifier,
        line_start=raw_import["line_start"],
        import_kind=raw_import["import_kind"],
        language=raw_import["language"],
        resolved_file=resolved_file,
        dependency_type=dependency_type,
        matched_dependency=matched_dependency,
    )


def resolve_javascript_path(project_root: Path, base_path: Path) -> str | None:
    candidates = [base_path]

    if base_path.suffix:
        candidates.append(base_path.with_suffix(base_path.suffix))
    else:
        for extension in JS_RESOLUTION_EXTENSIONS:
            candidates.append(base_path.with_suffix(extension))

    if base_path.is_dir() or not base_path.suffix:
        for extension in JS_RESOLUTION_EXTENSIONS:
            candidates.append(base_path / f"index{extension}")

    for candidate in candidates:
        if candidate.is_file():
            return to_posix_path(candidate.resolve().relative_to(project_root.resolve()))
    return None


def build_import_edge(
    source_file: str,
    imported: str,
    line_start: int,
    import_kind: str,
    language: str,
    resolved_file: str | None,
    dependency_type: str,
    matched_dependency: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "imported": imported,
        "line_start": line_start,
        "resolved": resolved_file is not None,
        "resolved_file": resolved_file,
        "dependency_type": dependency_type,
        "is_internal": dependency_type == "internal",
        "is_external": dependency_type != "internal",
        "import_kind": import_kind,
        "language": language,
        "matched_dependency": [
            {
                "name": item["name"],
                "source_file": item["source_file"],
                "manifest_type": item["manifest_type"],
            }
            for item in matched_dependency or []
        ]
        or None,
    }


def collect_external_dependencies(project_root: Path, inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []

    for record in inventory:
        relative = record["path"]
        absolute = project_root / relative
        filename = PurePosixPath(relative).name

        if filename == "package.json":
            dependencies.extend(parse_package_json_dependencies(absolute, relative))
        elif filename == "requirements.txt":
            dependencies.extend(parse_requirements_dependencies(absolute, relative))
        elif filename == "pyproject.toml":
            dependencies.extend(parse_pyproject_dependencies(absolute, relative))

    dependencies.sort(key=lambda item: (item["ecosystem"], item["name"], item["source_file"], item["manifest_type"]))
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for dependency in dependencies:
        key = (dependency["ecosystem"], dependency["name"], dependency["source_file"], dependency["manifest_type"])
        deduped[key] = dependency
    return list(deduped.values())


def parse_package_json_dependencies(path: Path, relative: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    dependencies: list[dict[str, Any]] = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        for name, version in payload.get(section, {}).items():
            dependencies.append(
                {
                    "name": name,
                    "normalized_name": normalize_node_dependency_name(name),
                    "version": str(version),
                    "source_file": relative,
                    "manifest_type": f"package.json:{section}",
                    "ecosystem": "node",
                }
            )
    return dependencies


def parse_requirements_dependencies(path: Path, relative: str) -> list[dict[str, Any]]:
    lines = read_text_file(path).splitlines()
    dependencies: list[dict[str, Any]] = []
    pattern = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*([<>=!~].*)?$")

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = pattern.match(stripped)
        if not match:
            continue
        name = match.group(1)
        dependencies.append(
            {
                "name": name,
                "normalized_name": normalize_python_dependency_name(name),
                "version": (match.group(2) or "").strip() or None,
                "source_file": relative,
                "manifest_type": "requirements.txt",
                "ecosystem": "python",
            }
        )
    return dependencies


def parse_pyproject_dependencies(path: Path, relative: str) -> list[dict[str, Any]]:
    try:
        payload = tomllib.loads(read_text_file(path))
    except tomllib.TOMLDecodeError:
        return []

    dependencies: list[dict[str, Any]] = []

    for item in payload.get("project", {}).get("dependencies", []):
        name, version = split_dependency_spec(item)
        dependencies.append(
            {
                "name": name,
                "normalized_name": normalize_python_dependency_name(name),
                "version": version,
                "source_file": relative,
                "manifest_type": "pyproject.toml:project.dependencies",
                "ecosystem": "python",
            }
        )

    optional_dependencies = payload.get("project", {}).get("optional-dependencies", {})
    for group_name, items in optional_dependencies.items():
        for item in items:
            name, version = split_dependency_spec(item)
            dependencies.append(
                {
                    "name": name,
                    "normalized_name": normalize_python_dependency_name(name),
                    "version": version,
                    "source_file": relative,
                    "manifest_type": f"pyproject.toml:project.optional-dependencies.{group_name}",
                    "ecosystem": "python",
                }
            )

    poetry_dependencies = payload.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, version in poetry_dependencies.items():
        if name == "python":
            continue
        dependencies.append(
            {
                "name": name,
                "normalized_name": normalize_python_dependency_name(name),
                "version": None if isinstance(version, dict) else str(version),
                "source_file": relative,
                "manifest_type": "pyproject.toml:tool.poetry.dependencies",
                "ecosystem": "python",
            }
        )

    return dependencies


def split_dependency_spec(spec: str) -> tuple[str, str | None]:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+(?:\[[^\]]+\])?)\s*(.*)$", spec)
    if not match:
        return spec.strip(), None
    name = match.group(1)
    version = match.group(2).strip() or None
    return name, version


def build_third_party_index(
    external_dependencies: list[dict[str, Any]]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    index: dict[str, dict[str, list[dict[str, Any]]]] = {
        "python": defaultdict(list),
        "node": defaultdict(list),
    }
    for dependency in external_dependencies:
        index[dependency["ecosystem"]][dependency["normalized_name"]].append(dependency)
    return {
        "python": dict(index["python"]),
        "node": dict(index["node"]),
    }


def build_test_relations(inventory: list[dict[str, Any]], imports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inventory_by_path = {record["path"]: record for record in inventory}
    source_files = {
        record["path"]
        for record in inventory
        if record["file_type"] == "source"
    }
    relations_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for edge in imports:
        if edge["dependency_type"] != "internal" or not edge["resolved_file"]:
            continue
        source_record = inventory_by_path.get(edge["source_file"])
        target_record = inventory_by_path.get(edge["resolved_file"])
        if not source_record or not target_record:
            continue
        if source_record["is_test"] and edge["resolved_file"] in source_files:
            relations_by_key[(edge["source_file"], edge["resolved_file"])] = {
                "relation_type": "tests",
                "source": edge["source_file"],
                "target": edge["resolved_file"],
                "confidence": "high",
                "reason": "test file imports the source file directly",
            }

    normalized_sources: dict[str, list[str]] = defaultdict(list)
    for file_path in source_files:
        normalized_sources[normalized_stem(file_path)].append(file_path)

    for record in inventory:
        if not record["is_test"]:
            continue
        test_path = record["path"]
        if any(source == test_path for source, _ in relations_by_key):
            continue
        stem = normalized_stem(test_path)
        if stem in GENERIC_HEURISTIC_STEMS:
            continue
        candidates = normalized_sources.get(stem, [])
        if len(candidates) == 1:
            relations_by_key[(test_path, candidates[0])] = {
                "relation_type": "tests",
                "source": test_path,
                "target": candidates[0],
                "confidence": "medium",
                "reason": "test and source file share a normalized stem",
            }

    relations = sorted(
        relations_by_key.values(),
        key=lambda item: (item["target"], item["source"]),
    )
    return relations


def build_module_candidates(
    generated_at: str,
    project_root: Path,
    inventory: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    test_relations: list[dict[str, Any]],
) -> dict[str, Any]:
    records_by_path = {record["path"]: record for record in inventory}
    internal_edges = [
        edge
        for edge in imports
        if edge["dependency_type"] == "internal" and edge["resolved_file"]
    ]
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in internal_edges:
        adjacency[edge["source_file"]].add(edge["resolved_file"])

    candidates: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    relation_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in test_relations:
        relation_by_target[relation["target"]].append(relation)

    entrypoint_files = [record["path"] for record in inventory if record["is_possible_entrypoint"]]
    for entrypoint in sorted(entrypoint_files):
        reachable = walk_internal_graph(entrypoint, adjacency)
        candidate_records = [records_by_path[file_path] for file_path in sorted(reachable) if file_path in records_by_path]
        entry_relations = [
            relation
            for relation in test_relations
            if relation["target"] in reachable or relation["source"] in reachable
        ]
        reasons = [
            f"seeded from possible entry point '{entrypoint}'",
            f"candidate includes {count_file_type(candidate_records, 'source')} source file(s) reachable via internal imports",
        ]
        warnings = [] if len(reachable) > 1 else ["entry point has no resolved internal imports"]
        add_candidate(
            candidates,
            name=f"entry:{PurePosixPath(entrypoint).stem}",
            candidate_type="entrypoint",
            records=candidate_records,
            reasons=reasons,
            confidence="high" if len(reachable) >= 3 else "medium",
            warnings=warnings,
            relations=entry_relations,
            related_files=[],
        )

    package_dirs = sorted(
        str(PurePosixPath(record["path"]).parent)
        for record in inventory
        if PurePosixPath(record["path"]).name == "__init__.py"
    )
    for package_dir in dict.fromkeys(package_dirs):
        candidate_records = subtree_records(inventory, package_dir)
        package_files = [record["path"] for record in candidate_records]
        internal_edge_count = count_internal_edges_within(package_files, internal_edges)
        reasons = [
            f"package directory '{package_dir}' contains __init__.py",
            f"candidate includes {count_file_type(candidate_records, 'source')} source file(s) under the package subtree",
        ]
        if internal_edge_count:
            reasons.append(f"candidate contains {internal_edge_count} internal import edge(s) among included files")
        warnings = [] if count_file_type(candidate_records, "source") > 1 else ["package subtree is currently small"]
        add_candidate(
            candidates,
            name=PurePosixPath(package_dir).name,
            candidate_type="package",
            records=candidate_records,
            reasons=reasons,
            confidence="high" if count_file_type(candidate_records, "source") >= 2 else "medium",
            warnings=warnings,
            relations=[relation for relation in test_relations if relation["target"] in package_files or relation["source"] in package_files],
            related_files=[],
        )

    top_level_directories = sorted(
        {
            PurePosixPath(record["path"]).parts[0]
            for record in inventory
            if len(PurePosixPath(record["path"]).parts) > 1
        }
    )
    package_dir_set = set(package_dirs)
    for directory in top_level_directories:
        if directory in package_dir_set:
            continue
        candidate_records = subtree_records(inventory, directory)
        if not any(record["file_type"] in {"source", "test"} for record in candidate_records):
            continue
        candidate_files = [record["path"] for record in candidate_records]
        internal_edge_count = count_internal_edges_within(candidate_files, internal_edges)
        reasons = [
            f"candidate covers subtree '{directory}'",
            f"candidate includes {count_file_type(candidate_records, 'source')} source file(s) and {count_file_type(candidate_records, 'test')} test file(s)",
        ]
        if internal_edge_count:
            reasons.append(f"candidate contains {internal_edge_count} internal import edge(s) among included files")
        if any(record["is_possible_entrypoint"] for record in candidate_records):
            reasons.append("candidate includes at least one possible entry point")
        warnings = []
        if count_file_type(candidate_records, "source") <= 1:
            warnings.append("directory candidate is shallow and mostly rooted in layout")
        if count_file_type(candidate_records, "source") and count_file_type(candidate_records, "test"):
            warnings.append("directory candidate mixes source and test files; use separated file lists and relations")
        add_candidate(
            candidates,
            name=directory,
            candidate_type="directory",
            records=candidate_records,
            reasons=reasons,
            confidence="medium" if count_file_type(candidate_records, "source") or count_file_type(candidate_records, "test") else "low",
            warnings=warnings,
            relations=[relation for relation in test_relations if relation["target"] in candidate_files or relation["source"] in candidate_files],
            related_files=[],
        )

    grouped_relations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relation in test_relations:
        grouped_relations[relation["target"]].append(relation)

    for target, relations in grouped_relations.items():
        related_records = [records_by_path[target]]
        for relation in relations:
            if relation["source"] in records_by_path:
                related_records.append(records_by_path[relation["source"]])
        reasons = [
            f"candidate links {len(relations)} test file(s) to the production target '{target}'",
            f"candidate includes {count_file_type(related_records, 'source')} source file(s) and {count_file_type(related_records, 'test')} test file(s)",
        ]
        warnings = []
        if any(relation["confidence"] != "high" for relation in relations):
            warnings.append("some test relations are heuristic rather than import-based")
        add_candidate(
            candidates,
            name=normalized_stem(target),
            candidate_type="test_target",
            records=deduplicate_records(related_records),
            reasons=reasons,
            confidence="high" if all(relation["confidence"] == "high" for relation in relations) else "medium",
            warnings=warnings,
            relations=relations,
            related_files=[],
        )

    source_name_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in inventory:
        if record["file_type"] != "source":
            continue
        source_name_groups[normalized_stem(record["path"])].append(record)

    for stem, records in source_name_groups.items():
        if len(records) < 2:
            continue
        if stem in GENERIC_HEURISTIC_STEMS:
            continue
        reasons = [
            f"multiple production files share normalized stem '{stem}'",
            f"candidate includes {count_file_type(records, 'source')} source file(s)",
        ]
        warnings = []
        if len({str(PurePosixPath(record['path']).parent) for record in records}) > 2:
            warnings.append("cluster spans many directories")
        add_candidate(
            candidates,
            name=stem,
            candidate_type="name_cluster",
            records=records,
            reasons=reasons,
            confidence="low",
            warnings=warnings,
            relations=[],
            related_files=[],
        )

    ordered_candidates = sorted(
        candidates.values(),
        key=lambda candidate: (
            -CONFIDENCE_ORDER[candidate["confidence"]],
            candidate["type"],
            candidate["name"],
            candidate["files"],
        ),
    )
    relations = sorted(test_relations, key=lambda relation: (relation["target"], relation["source"]))
    return artifact_payload(
        generated_at=generated_at,
        project_root=project_root,
        candidates=ordered_candidates,
        relations=relations,
    )


def subtree_records(inventory: list[dict[str, Any]], directory: str) -> list[dict[str, Any]]:
    prefix = f"{directory}/"
    return [record for record in inventory if record["path"] == directory or record["path"].startswith(prefix)]


def count_internal_edges_within(files: list[str], internal_edges: list[dict[str, Any]]) -> int:
    file_set = set(files)
    return sum(1 for edge in internal_edges if edge["source_file"] in file_set and edge["resolved_file"] in file_set)


def count_file_type(records: list[dict[str, Any]], file_type: str) -> int:
    return sum(1 for record in records if record["file_type"] == file_type)


def add_candidate(
    candidates: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]],
    name: str,
    candidate_type: str,
    records: list[dict[str, Any]],
    reasons: list[str],
    confidence: str,
    warnings: list[str],
    relations: list[dict[str, Any]],
    related_files: list[str],
) -> None:
    categorized = categorize_candidate_files(records)
    all_files = categorized["files"]
    final_type = derive_candidate_type(candidate_type, records)
    fixture_ratio = (
        sum(1 for record in records if record.get("is_fixture")) / len(records)
        if records
        else 0
    )
    key = (final_type, name, tuple(all_files))
    relation_payload = sorted(
        relations,
        key=lambda relation: (relation["target"], relation["source"]),
    )
    relation_related_files = {
        endpoint
        for relation in relation_payload
        for endpoint in (relation["source"], relation["target"])
        if endpoint not in all_files
    }
    related_files = sorted({*related_files, *relation_related_files})

    payload = {
        "name": name,
        "type": final_type,
        "files": all_files,
        "source_files": categorized["source_files"],
        "test_files": categorized["test_files"],
        "config_files": categorized["config_files"],
        "doc_files": categorized["doc_files"],
        "other_files": categorized["other_files"],
        "related_files": related_files,
        "reasons": sorted(
            set(
                reasons
                + ([f"candidate is dominated by fixture/sample files ({fixture_ratio:.0%})"] if final_type in {"fixture", "test_asset"} else [])
            )
        ),
        "confidence": confidence,
        "warnings": sorted(
            set(
                warnings
                + (["candidate is not suitable as a production module without fixture-aware filtering"] if final_type in {"fixture", "test_asset"} else [])
            )
        ),
        "relations": relation_payload,
    }

    if key not in candidates:
        candidates[key] = payload
        return

    candidate = candidates[key]
    candidate["reasons"] = sorted(set(candidate["reasons"] + payload["reasons"]))
    candidate["warnings"] = sorted(set(candidate["warnings"] + payload["warnings"]))
    candidate["related_files"] = sorted(set(candidate["related_files"] + payload["related_files"]))
    candidate["relations"] = deduplicate_relations(candidate["relations"] + payload["relations"])
    if CONFIDENCE_ORDER[payload["confidence"]] > CONFIDENCE_ORDER[candidate["confidence"]]:
        candidate["confidence"] = payload["confidence"]


def derive_candidate_type(base_type: str, records: list[dict[str, Any]]) -> str:
    if not records:
        return base_type

    fixture_count = sum(1 for record in records if record.get("is_fixture"))
    if fixture_count == 0:
        return base_type

    fixture_ratio = fixture_count / len(records)
    if fixture_ratio >= 0.6:
        if base_type == "test_target" or any(record["file_type"] == "test" for record in records):
            return "test_asset"
        return "fixture"
    return base_type


def categorize_candidate_files(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    categorized = {
        "source_files": sorted(record["path"] for record in records if record["file_type"] == "source"),
        "test_files": sorted(record["path"] for record in records if record["file_type"] == "test"),
        "config_files": sorted(record["path"] for record in records if record["file_type"] == "config"),
        "doc_files": sorted(record["path"] for record in records if record["file_type"] == "doc"),
        "other_files": sorted(
            record["path"]
            for record in records
            if record["file_type"] not in {"source", "test", "config", "doc"}
        ),
    }
    categorized["files"] = sorted(
        {
            *categorized["source_files"],
            *categorized["test_files"],
            *categorized["config_files"],
            *categorized["doc_files"],
            *categorized["other_files"],
        }
    )
    return categorized


def deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        deduped[record["path"]] = record
    return [deduped[path] for path in sorted(deduped)]


def deduplicate_relations(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for relation in relations:
        key = (relation["relation_type"], relation["source"], relation["target"])
        deduped[key] = relation
    return [deduped[key] for key in sorted(deduped)]


def walk_internal_graph(entrypoint: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([entrypoint])

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for neighbor in adjacency.get(current, set()):
            if neighbor not in visited:
                queue.append(neighbor)

    return visited


def normalized_stem(file_path: str) -> str:
    stem = PurePosixPath(file_path).stem.casefold()
    if stem.startswith("test_"):
        stem = stem[5:]
    if stem.endswith("_test"):
        stem = stem[:-5]
    if stem.endswith(".test"):
        stem = stem[:-5]
    if stem.endswith(".spec"):
        stem = stem[:-5]
    return stem or PurePosixPath(file_path).stem.casefold()


def build_analysis_summary(
    generated_at: str,
    project_root: Path,
    output_root: Path,
    inventory: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    limitations: list[str],
    coverage_report: dict[str, Any],
) -> dict[str, Any]:
    detected_languages = sorted({record["language"] for record in inventory if record["language"] != "unknown"})
    applied_analyzers = ["inventory"]

    if any(record["language"] == "python" for record in inventory):
        applied_analyzers.append("python-ast")
    if any(record["language"] in {"javascript", "typescript"} for record in inventory):
        applied_analyzers.append("javascript-typescript-regex")
    applied_analyzers.extend(["dependency-resolution", "manifest-reader", "module-heuristics", "coverage"])

    normalized_limitations = normalize_limitations(limitations)

    return artifact_payload(
        generated_at=generated_at,
        project_root=project_root,
        analysis_date=generated_at,
        artifacts_path=str(output_root),
        file_count=len(inventory),
        source_file_count=sum(1 for record in inventory if record["file_type"] == "source"),
        test_file_count=sum(1 for record in inventory if record["is_test"]),
        config_file_count=sum(1 for record in inventory if record["is_config"]),
        entity_count=len(entities),
        import_count=len(imports),
        detected_languages=detected_languages,
        applied_analyzers=applied_analyzers,
        limitations=normalized_limitations,
        deep_analyzed_file_count=coverage_report["deep_analyzed_file_count"],
        shallow_indexed_file_count=coverage_report["shallow_indexed_file_count"],
    )


def build_coverage_report(
    generated_at: str,
    project_root: Path,
    inventory: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    deep_analyzed_files: set[str],
    limitations: list[str],
) -> dict[str, Any]:
    unsupported_extensions = sorted(
        {
            record["extension"]
            for record in inventory
            if record["extension"]
            and not record["supports_deep_analysis"]
        }
    )
    supported_languages = sorted(SUPPORTED_DEEP_LANGUAGES)
    detected_supported_languages = sorted(
        {
            record["language"]
            for record in inventory
            if record["language"] in SUPPORTED_DEEP_LANGUAGES
        }
    )

    return artifact_payload(
        generated_at=generated_at,
        project_root=project_root,
        indexed_file_count=len(inventory),
        deep_analyzed_file_count=len(deep_analyzed_files),
        shallow_indexed_file_count=len(inventory) - len(deep_analyzed_files),
        unsupported_deep_extensions=unsupported_extensions,
        supported_languages=supported_languages,
        detected_supported_languages=detected_supported_languages,
        unresolved_import_count=sum(1 for edge in imports if edge["dependency_type"] == "unresolved"),
        low_confidence_entity_count=sum(1 for entity in entities if entity["confidence"] == "low"),
        limitations=normalize_limitations(limitations),
    )


def build_artifact_manifest(
    generated_at: str,
    project_root: Path,
    output_root: Path,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    purposes = {
        "inventory.json": "Project file inventory with coarse classification and analysis depth.",
        "function-index.json": "Index of discovered functions, methods, classes, and extracted signatures.",
        "dependency-graph.json": "Resolved import graph with dependency typing and external manifest dependencies.",
        "module-candidates.json": "Heuristic logical module candidates and test relations.",
        "analysis-summary.json": "High-level analysis totals, detected languages, and limitations.",
        "coverage-report.json": "Coverage of deep analysis versus shallow indexing and confidence gaps.",
        "artifact-manifest.json": "Manifest describing generated analysis artifacts.",
    }

    entries = []
    for filename, payload in artifacts.items():
        entries.append(
            {
                "artifact": filename,
                "path": filename,
                "absolute_path": str(output_root / filename),
                "schema_version": payload["schema_version"],
                "purpose": purposes[filename],
                "record_count": count_artifact_records(filename, payload),
                "warnings": artifact_warnings(filename, payload),
            }
        )

    manifest_entry = {
        "artifact": "artifact-manifest.json",
        "path": "artifact-manifest.json",
        "absolute_path": str(output_root / "artifact-manifest.json"),
        "schema_version": SCHEMA_VERSION,
        "purpose": purposes["artifact-manifest.json"],
        "record_count": len(entries) + 1,
        "warnings": [],
    }
    entries.append(manifest_entry)

    return artifact_payload(
        generated_at=generated_at,
        project_root=project_root,
        artifacts=entries,
    )


def count_artifact_records(filename: str, payload: dict[str, Any]) -> int:
    if filename == "inventory.json":
        return len(payload["files"])
    if filename == "function-index.json":
        return len(payload["entities"])
    if filename == "dependency-graph.json":
        return len(payload["imports"])
    if filename == "module-candidates.json":
        return len(payload["candidates"])
    if filename == "analysis-summary.json":
        return 1
    if filename == "coverage-report.json":
        return 1
    if filename == "artifact-manifest.json":
        return len(payload["artifacts"])
    return 0


def artifact_warnings(filename: str, payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if filename == "inventory.json":
        unknown_count = sum(1 for record in payload["files"] if record["language"] == "unknown")
        if unknown_count:
            warnings.append(f"{unknown_count} file(s) have unknown language classification")
        fixture_count = sum(1 for record in payload["files"] if record.get("is_fixture"))
        if fixture_count:
            warnings.append(f"{fixture_count} file(s) are marked as fixture/sample content")
        generated_count = sum(1 for record in payload["files"] if record.get("is_generated") or record.get("is_packaging_metadata"))
        if generated_count:
            warnings.append(f"{generated_count} file(s) are generated or packaging metadata")
    elif filename == "function-index.json":
        low_confidence = sum(1 for entity in payload["entities"] if entity["confidence"] == "low")
        if low_confidence:
            warnings.append(f"{low_confidence} entity record(s) are low confidence")
    elif filename == "dependency-graph.json":
        unresolved = sum(1 for edge in payload["imports"] if edge["dependency_type"] == "unresolved")
        if unresolved:
            warnings.append(f"{unresolved} import(s) remain unresolved")
    elif filename == "module-candidates.json":
        candidate_warnings = sum(1 for candidate in payload["candidates"] if candidate["warnings"])
        if candidate_warnings:
            warnings.append(f"{candidate_warnings} candidate(s) include heuristic warnings")
    elif filename == "analysis-summary.json":
        if payload["limitations"]:
            warnings.append("summary includes analysis limitations")
    elif filename == "coverage-report.json":
        if payload["unsupported_deep_extensions"]:
            warnings.append("some file extensions are only shallowly indexed")
    return warnings


def normalize_limitations(limitations: list[str]) -> list[str]:
    return sorted(
        set(
            [
                "JavaScript/TypeScript entity extraction is regex-based and approximate.",
                "Dynamic imports and runtime-generated dependencies are not resolved.",
                "Unknown file types are indexed without deep semantic analysis.",
                *[limitation for limitation in limitations if limitation],
            ]
        )
    )


def sort_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entities, key=lambda item: (item["file"], item["line_start"], item["name"], item["entity_type"]))


def deduplicate_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for entity in entities:
        key = (entity["file"], entity["name"], entity["line_start"], entity["entity_type"])
        deduped[key] = entity
    return list(deduped.values())


def count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return dict(counts)


def normalize_python_dependency_name(name: str) -> str:
    return name.split("[", 1)[0].lower().replace("-", "_").replace(".", "_")


def normalize_node_dependency_name(specifier: str) -> str:
    stripped = strip_node_prefix(specifier)
    if stripped.startswith("@"):
        parts = stripped.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else stripped
    return stripped.split("/", 1)[0]


def strip_node_prefix(specifier: str) -> str:
    return specifier[5:] if specifier.startswith("node:") else specifier


def safe_unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def normalize_inline_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def normalize_path_text(path_text: str) -> str:
    normalized = path_text.replace("\\", "/").casefold()
    if not normalized.endswith("/"):
        normalized = f"{normalized}/"
    return normalized


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def path_depth(path: str) -> int:
    if path in {"", "."}:
        return 0
    return len(PurePosixPath(path).parts)


def path_in_directory(path: str, directory: str) -> bool:
    if directory in {"", "."}:
        return True
    prefix = f"{directory}/"
    return path == directory or path.startswith(prefix)


def relative_within(path: str, directory: str) -> str:
    if directory in {"", "."}:
        return path
    return to_posix_path(PurePosixPath(path).relative_to(PurePosixPath(directory)))


def python_module_name_from_relative_path(relative_path: str) -> str:
    pure_path = PurePosixPath(relative_path)
    parts = list(pure_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def to_posix_path(path: Path) -> str:
    return PurePosixPath(path).as_posix()
