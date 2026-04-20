from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.renderer import key_entity_sort_key  # noqa: E402

FIXTURE_PROJECT = ROOT / "tests" / "fixtures" / "sample_project"
REQUIRED_ANALYSIS_ARTIFACTS = (
    "inventory.json",
    "function-index.json",
    "dependency-graph.json",
    "module-candidates.json",
    "analysis-summary.json",
    "artifact-manifest.json",
    "coverage-report.json",
)


class RendererCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        pythonpath = str(SRC_ROOT)
        if environment.get("PYTHONPATH"):
            pythonpath = f"{pythonpath}{os.pathsep}{environment['PYTHONPATH']}"
        environment["PYTHONPATH"] = pythonpath
        return subprocess.run(
            [sys.executable, "-m", "docgen", *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_temp_dir(self) -> Path:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        return Path(temp_directory.name)

    def run_analysis(self, project_path: Path, *extra_args: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        temp_root = self.make_temp_dir()
        analysis_dir = temp_root / "analysis"
        result = self.run_cli("analyze", str(project_path), "--output", str(analysis_dir), *extra_args)
        return result, analysis_dir

    def run_render(self, analysis_dir: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
        return self.run_cli("render", "--analysis", str(analysis_dir), "--output", str(output_dir))

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def read_json(self, path: Path) -> dict:
        return json.loads(self.read_text(path))

    def force_remove_readonly(self, func, path, exc_info) -> None:  # type: ignore[no-untyped-def]
        os.chmod(path, 0o700)
        func(path)

    def build_minimal_analysis(self, analysis_dir: Path, *, overrides: dict[str, dict] | None = None) -> Path:
        analysis_dir.mkdir(parents=True, exist_ok=True)
        project_path = str((analysis_dir / ".." / "project").resolve())
        artifacts = {
            "inventory.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "files": [
                    {
                        "path": "src/example.py",
                        "extension": ".py",
                        "file_type": "source",
                        "size_bytes": 12,
                        "language": "python",
                        "is_test": False,
                        "is_config": False,
                        "is_possible_entrypoint": False,
                        "artifact_role": None,
                        "is_fixture": False,
                        "is_generated": False,
                        "is_packaging_metadata": False,
                        "analysis_depth": "deep",
                        "supports_deep_analysis": True,
                    }
                ],
            },
            "function-index.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "entities": [
                    {
                        "name": "build_message",
                        "entity_type": "function",
                        "type": "function",
                        "file": "src/example.py",
                        "parent": None,
                        "container": "src.example",
                        "signature": "def build_message(name: str) -> str",
                        "parameters": [{"name": "name", "annotation": "str", "default": None}],
                        "return_annotation": "str",
                        "is_async": False,
                        "exported": True,
                        "docstring": "Synthetic example.",
                        "confidence": "high",
                        "line_start": 1,
                    }
                ],
            },
            "dependency-graph.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "imports": [
                    {
                        "source_file": "src/example.py",
                        "imported": "json",
                        "dependency_type": "stdlib",
                        "resolved_file": None,
                        "is_internal": False,
                        "is_external": False,
                    }
                ],
                "external_dependencies": [],
                "dependency_type_counts": {"stdlib": 1},
            },
            "module-candidates.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "candidates": [
                    {
                        "name": "example",
                        "type": "package",
                        "confidence": "high",
                        "files": ["src/example.py"],
                        "source_files": ["src/example.py"],
                        "test_files": [],
                        "config_files": [],
                        "doc_files": [],
                        "other_files": [],
                        "related_files": [],
                        "reasons": ["synthetic module"],
                        "warnings": [],
                        "relations": [],
                    }
                ],
                "relations": [],
            },
            "analysis-summary.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "analysis_date": "2026-04-20",
                "file_count": 1,
                "source_file_count": 1,
                "test_file_count": 0,
                "config_file_count": 0,
                "entity_count": 1,
                "import_count": 1,
                "deep_analyzed_file_count": 1,
                "shallow_indexed_file_count": 0,
                "detected_languages": ["python"],
                "applied_analyzers": ["synthetic"],
                "limitations": [],
            },
            "artifact-manifest.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "artifacts": [],
            },
            "coverage-report.json": {
                "schema_version": "1.0",
                "generated_at": "2026-04-20T00:00:00+00:00",
                "project_path": project_path,
                "tool_version": "0.1.0",
                "indexed_file_count": 1,
                "deep_analyzed_file_count": 1,
                "shallow_indexed_file_count": 0,
                "unsupported_deep_extensions": [],
                "supported_languages": ["python"],
                "detected_supported_languages": ["python"],
                "unresolved_import_count": 0,
                "low_confidence_entity_count": 0,
                "limitations": ["synthetic limitation"],
            },
        }

        for artifact_name, artifact_override in (overrides or {}).items():
            artifacts[artifact_name] = artifact_override

        for artifact_name, payload in artifacts.items():
            (analysis_dir / artifact_name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return analysis_dir

    def extract_section(self, text: str, heading: str) -> str:
        anchor = text.index(heading)
        remainder = text[anchor + len(heading) :]
        next_heading = remainder.find("\n## ")
        if next_heading == -1:
            return remainder.strip()
        return remainder[:next_heading].strip()

    def count_table_data_rows(self, section: str) -> int:
        rows = [line for line in section.splitlines() if line.startswith("| ")]
        if len(rows) < 2:
            return 0
        return len(rows) - 2

    def test_render_command_creates_expected_docs_and_file_navigation_from_fixture_analysis(self) -> None:
        analyze_result, analysis_dir = self.run_analysis(FIXTURE_PROJECT)
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)

        output_dir = self.make_temp_dir() / "docs-generated"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)
        self.assertIn("Rendered documentation saved to:", render_result.stdout)

        expected_paths = [
            output_dir / "index.md",
            output_dir / "architecture.md",
            output_dir / "module-map.md",
            output_dir / "dependency-map.md",
            output_dir / "coverage-report.md",
            output_dir / "doc-manifest.json",
            output_dir / "functions" / "function-index.md",
            output_dir / "files" / "index.md",
        ]
        for path in expected_paths:
            self.assertTrue(path.exists(), path)

        module_files = sorted(output_dir.glob("modules/*.md"))
        file_pages = sorted(output_dir.glob("files/*.md"))
        self.assertTrue(module_files)
        self.assertTrue(any(path.name != "index.md" for path in file_pages))
        self.assertTrue(all(":" not in path.name and " " not in path.name for path in module_files))
        self.assertTrue(all(":" not in path.name and " " not in path.name for path in file_pages))

        index_text = self.read_text(output_dir / "index.md")
        self.assertIn("[Индекс файлов](files/index.md)", index_text)
        self.assertIn("## Режим анализа", index_text)

        module_map_text = self.read_text(output_dir / "module-map.md")
        self.assertIn("module_page_role", module_map_text)
        self.assertIn("entry:index", module_map_text)

        functions_text = self.read_text(output_dir / "functions" / "function-index.md")
        self.assertIn("build_message", functions_text)
        self.assertIn("../files/", functions_text)

        files_index_text = self.read_text(output_dir / "files" / "index.md")
        self.assertIn("python_pkg/core.py", files_index_text)

        manifest = self.read_json(output_dir / "doc-manifest.json")
        fixture_module_path = next(
            path for path in manifest["generated_files"] if path.startswith("modules/") and "entry-index" in path
        )
        fixture_module_text = self.read_text(output_dir / fixture_module_path)
        self.assertIn("## Роль страницы", fixture_module_text)
        self.assertIn("test", fixture_module_text)
        self.assertIn("Это тестовый/примерный артефакт, а не production-модуль.", fixture_module_text)

        self.assertEqual(manifest["generated_file_count"], len(manifest["generated_files"]))
        self.assertGreaterEqual(manifest["file_page_count"], 1)
        self.assertGreaterEqual(manifest["module_page_count"], 1)
        self.assertEqual(manifest["function_index_path"], "functions/function-index.md")
        self.assertEqual(manifest["file_index_path"], "files/index.md")
        self.assertEqual(manifest["documentation_layout_version"], "2.1")

    def test_render_creates_file_page_for_import_only_file(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        self.build_minimal_analysis(
            analysis_dir,
            overrides={
                "inventory.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "files": [
                        {
                            "path": "src/example.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 12,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                        {
                            "path": "src/import_only.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 8,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                    ],
                },
                "dependency-graph.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "imports": [
                        {
                            "source_file": "src/example.py",
                            "imported": "json",
                            "dependency_type": "stdlib",
                            "resolved_file": None,
                            "is_internal": False,
                            "is_external": False,
                        },
                        {
                            "source_file": "src/import_only.py",
                            "imported": "pathlib",
                            "dependency_type": "stdlib",
                            "resolved_file": None,
                            "is_internal": False,
                            "is_external": False,
                        },
                    ],
                    "external_dependencies": [],
                    "dependency_type_counts": {"stdlib": 2},
                },
            },
        )
        output_dir = self.make_temp_dir() / "docs"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        manifest = self.read_json(output_dir / "doc-manifest.json")
        import_only_page = next(path for path in manifest["generated_files"] if "import-only" in path)
        page_text = self.read_text(output_dir / import_only_page)
        self.assertIn("# Файл: src/import_only.py", page_text)
        self.assertIn("## Сущности", page_text)
        self.assertIn("нет данных", page_text)
        self.assertIn("pathlib", page_text)

    def test_render_manifest_contains_file_pages_and_module_pages_for_live_facts(self) -> None:
        analyze_result, analysis_dir = self.run_analysis(ROOT)
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)

        output_dir = self.make_temp_dir() / "docs-generated"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        manifest = self.read_json(output_dir / "doc-manifest.json")
        self.assertIn("file_pages", manifest)
        self.assertIn("module_pages", manifest)
        self.assertTrue(manifest["file_pages"])
        self.assertTrue(manifest["module_pages"])

        analysis_function_index = self.read_json(analysis_dir / "function-index.json")
        analysis_dependency_graph = self.read_json(analysis_dir / "dependency-graph.json")
        fact_files = sorted(
            {
                str(entity.get("file") or "")
                for entity in analysis_function_index.get("entities", [])
                if entity.get("file")
            }
            | {
                str(import_entry.get("source_file") or "")
                for import_entry in analysis_dependency_graph.get("imports", [])
                if import_entry.get("source_file")
            }
        )
        mapped_files = {entry["source_file"] for entry in manifest["file_pages"]}
        self.assertTrue(set(fact_files).issubset(mapped_files))

        llm_entries = [
            entry
            for entry in manifest["file_pages"]
            if "src/docgen/llm/" in entry.get("source_file", "").replace("\\", "/")
        ]
        self.assertTrue(llm_entries)
        self.assertTrue(all(entry["doc_path"].startswith("files/") for entry in llm_entries))
        self.assertTrue(all(entry["doc_path"] in manifest["generated_files"] for entry in llm_entries))

        llm_module_entries = [entry for entry in manifest["module_pages"] if entry.get("name") == "llm"]
        self.assertTrue(llm_module_entries)
        self.assertTrue(all(entry["doc_path"].startswith("modules/") for entry in llm_module_entries))
        self.assertTrue(all(entry["doc_path"] in manifest["generated_files"] for entry in llm_module_entries))

    def test_key_entity_sort_key_prefers_exported_high_confidence_class(self) -> None:
        entities = [
            {
                "name": "helper",
                "entity_type": "function",
                "type": "function",
                "file": "b.py",
                "line_start": 20,
                "exported": False,
                "confidence": "medium",
            },
            {
                "name": "Service",
                "entity_type": "class",
                "type": "class",
                "file": "a.py",
                "line_start": 10,
                "exported": True,
                "confidence": "high",
            },
            {
                "name": "run",
                "entity_type": "function",
                "type": "function",
                "file": "a.py",
                "line_start": 5,
                "exported": True,
                "confidence": "medium",
            },
            {
                "name": "zeta",
                "entity_type": "method",
                "type": "method",
                "file": "a.py",
                "line_start": 1,
                "exported": None,
                "confidence": None,
            },
        ]
        sorted_names = [entity["name"] for entity in sorted(entities, key=key_entity_sort_key)]
        self.assertEqual(sorted_names, ["Service", "run", "helper", "zeta"])

    def test_render_limits_entities_for_page_roles_and_function_index(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        entities = []
        for index in range(40):
            entities.append(
                {
                    "name": f"entity_{index:02d}",
                    "entity_type": "function",
                    "type": "function",
                    "file": "src/many.py",
                    "container": "src.many",
                    "signature": f"def entity_{index:02d}() -> None",
                    "parameters": [],
                    "return_annotation": "None",
                    "is_async": False,
                    "exported": index < 5,
                    "docstring": None,
                    "confidence": "high" if index < 10 else "medium",
                    "line_start": index + 1,
                }
            )
        self.build_minimal_analysis(
            analysis_dir,
            overrides={
                "inventory.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "files": [
                        {
                            "path": "src/many.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 100,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": True,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                        {
                            "path": "tests/test_many.py",
                            "extension": ".py",
                            "file_type": "test",
                            "size_bytes": 20,
                            "language": "python",
                            "is_test": True,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                    ],
                },
                "function-index.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "entities": entities,
                },
                "dependency-graph.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "imports": [
                        {
                            "source_file": "src/many.py",
                            "imported": "json",
                            "dependency_type": "stdlib",
                            "resolved_file": None,
                            "is_internal": False,
                            "is_external": False,
                        }
                    ],
                    "external_dependencies": [],
                    "dependency_type_counts": {"stdlib": 1},
                },
                "module-candidates.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "candidates": [
                        {
                            "name": "pkg",
                            "type": "package",
                            "confidence": "high",
                            "files": ["src/many.py"],
                            "source_files": ["src/many.py"],
                            "test_files": [],
                            "config_files": [],
                            "doc_files": [],
                            "other_files": [],
                            "related_files": [],
                            "reasons": [],
                            "warnings": [],
                            "relations": [],
                        },
                        {
                            "name": "src",
                            "type": "directory",
                            "confidence": "medium",
                            "files": ["src/many.py"],
                            "source_files": ["src/many.py"],
                            "test_files": [],
                            "config_files": [],
                            "doc_files": [],
                            "other_files": [],
                            "related_files": [],
                            "reasons": [],
                            "warnings": [],
                            "relations": [],
                        },
                        {
                            "name": "entry:main",
                            "type": "entrypoint",
                            "confidence": "high",
                            "files": ["src/many.py"],
                            "source_files": ["src/many.py"],
                            "test_files": [],
                            "config_files": [],
                            "doc_files": [],
                            "other_files": [],
                            "related_files": [],
                            "reasons": [],
                            "warnings": [],
                            "relations": [],
                        },
                        {
                            "name": "tests",
                            "type": "test_asset",
                            "confidence": "medium",
                            "files": ["src/many.py", "tests/test_many.py"],
                            "source_files": ["src/many.py"],
                            "test_files": ["tests/test_many.py"],
                            "config_files": [],
                            "doc_files": [],
                            "other_files": [],
                            "related_files": [],
                            "reasons": [],
                            "warnings": [],
                            "relations": [],
                        },
                    ],
                    "relations": [],
                },
                "analysis-summary.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "analysis_date": "2026-04-20",
                    "file_count": 2,
                    "source_file_count": 1,
                    "test_file_count": 1,
                    "config_file_count": 0,
                    "entity_count": len(entities),
                    "import_count": 1,
                    "deep_analyzed_file_count": 2,
                    "shallow_indexed_file_count": 0,
                    "detected_languages": ["python"],
                    "applied_analyzers": ["synthetic"],
                    "limitations": [],
                },
                "coverage-report.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "indexed_file_count": 2,
                    "deep_analyzed_file_count": 2,
                    "shallow_indexed_file_count": 0,
                    "unsupported_deep_extensions": [],
                    "supported_languages": ["python"],
                    "detected_supported_languages": ["python"],
                    "unresolved_import_count": 0,
                    "low_confidence_entity_count": 0,
                    "limitations": [],
                },
            },
        )
        output_dir = self.make_temp_dir() / "docs"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        detailed_text = self.read_text(output_dir / "modules" / "module-package-pkg.md")
        aggregate_text = self.read_text(output_dir / "modules" / "module-directory-src.md")
        entrypoint_text = self.read_text(output_dir / "modules" / "module-entrypoint-entry-main.md")
        test_text = self.read_text(output_dir / "modules" / "module-test-asset-tests.md")
        functions_text = self.read_text(output_dir / "functions" / "function-index.md")

        detailed_rows = self.count_table_data_rows(self.extract_section(detailed_text, "## Связанные сущности"))
        aggregate_rows = self.count_table_data_rows(self.extract_section(aggregate_text, "## Ключевые сущности"))
        entrypoint_rows = self.count_table_data_rows(self.extract_section(entrypoint_text, "## Ключевые сущности"))
        test_rows = self.count_table_data_rows(self.extract_section(test_text, "## Ключевые сущности"))
        function_rows = self.count_table_data_rows(self.extract_section(functions_text, "## src/many.py"))

        self.assertLessEqual(detailed_rows, 30)
        self.assertLessEqual(aggregate_rows, 15)
        self.assertLessEqual(entrypoint_rows, 15)
        self.assertLessEqual(test_rows, 15)
        self.assertLessEqual(function_rows, 30)
        self.assertIn("Это обзорная aggregate-страница. Она не является полным индексом всех сущностей.", aggregate_text)
        self.assertIn("Это entrypoint-страница. Она показывает структурный обзор точки входа, а не полный индекс всех функций.", entrypoint_text)
        self.assertIn("Это тестовый/примерный артефакт, а не production-модуль.", test_text)
        self.assertIn("Показаны первые 30 сущностей.", detailed_text)

    def test_render_handles_missing_optional_fields_and_escaping(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        self.build_minimal_analysis(
            analysis_dir,
            overrides={
                "function-index.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "entities": [
                        {
                            "name": "escaped_entity",
                            "entity_type": "function",
                            "type": "function",
                            "file": "src/example.py",
                            "parameters": [{"name": "value", "annotation": "str", "default": None}],
                            "docstring": "alpha | beta\nsecond line",
                            "confidence": "medium",
                        }
                    ],
                }
            },
        )
        output_dir = self.make_temp_dir() / "docs"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        functions_text = self.read_text(output_dir / "functions" / "function-index.md")
        self.assertIn("alpha \\| beta<br>second line", functions_text)
        self.assertIn("нет данных", functions_text)

    def test_render_slug_collision_is_deterministic_and_collision_safe_for_modules_and_files(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        self.build_minimal_analysis(
            analysis_dir,
            overrides={
                "inventory.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "files": [
                        {
                            "path": "src/A-B.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 1,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                        {
                            "path": "src/A/B.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 1,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                    ],
                },
                "function-index.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "entities": [
                        {
                            "name": "one",
                            "entity_type": "function",
                            "type": "function",
                            "file": "src/A-B.py",
                            "container": None,
                            "parameters": [],
                            "confidence": "high",
                            "line_start": 1,
                        },
                        {
                            "name": "two",
                            "entity_type": "function",
                            "type": "function",
                            "file": "src/A/B.py",
                            "container": None,
                            "parameters": [],
                            "confidence": "high",
                            "line_start": 1,
                        },
                    ],
                },
                "dependency-graph.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "imports": [],
                    "external_dependencies": [],
                    "dependency_type_counts": {},
                },
                "module-candidates.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "candidates": [
                        {
                            "name": "Alpha/Beta",
                            "type": "package",
                            "confidence": "high",
                            "files": ["src/A-B.py"],
                            "source_files": ["src/A-B.py"],
                            "test_files": [],
                            "config_files": [],
                            "doc_files": [],
                            "other_files": [],
                            "related_files": [],
                            "reasons": [],
                            "warnings": [],
                            "relations": [],
                        },
                        {
                            "name": "Alpha:Beta",
                            "type": "package",
                            "confidence": "high",
                            "files": ["src/A/B.py"],
                            "source_files": ["src/A/B.py"],
                            "test_files": [],
                            "config_files": [],
                            "doc_files": [],
                            "other_files": [],
                            "related_files": [],
                            "reasons": [],
                            "warnings": [],
                            "relations": [],
                        },
                    ],
                    "relations": [],
                },
                "analysis-summary.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "analysis_date": "2026-04-20",
                    "file_count": 2,
                    "source_file_count": 2,
                    "test_file_count": 0,
                    "config_file_count": 0,
                    "entity_count": 2,
                    "import_count": 0,
                    "deep_analyzed_file_count": 2,
                    "shallow_indexed_file_count": 0,
                    "detected_languages": ["python"],
                    "applied_analyzers": ["synthetic"],
                    "limitations": [],
                },
                "coverage-report.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "indexed_file_count": 2,
                    "deep_analyzed_file_count": 2,
                    "shallow_indexed_file_count": 0,
                    "unsupported_deep_extensions": [],
                    "supported_languages": ["python"],
                    "detected_supported_languages": ["python"],
                    "unresolved_import_count": 0,
                    "low_confidence_entity_count": 0,
                    "limitations": [],
                },
            },
        )

        output_dir = self.make_temp_dir() / "docs"
        first_render = self.run_render(analysis_dir, output_dir)
        self.assertEqual(first_render.returncode, 0, msg=first_render.stderr or first_render.stdout)
        first_manifest = self.read_json(output_dir / "doc-manifest.json")
        second_render = self.run_render(analysis_dir, output_dir)
        self.assertEqual(second_render.returncode, 0, msg=second_render.stderr or second_render.stdout)
        second_manifest = self.read_json(output_dir / "doc-manifest.json")
        self.assertEqual(first_manifest["generated_files"], second_manifest["generated_files"])

        module_files = sorted(path.name for path in output_dir.glob("modules/*.md"))
        file_pages = sorted(path.name for path in output_dir.glob("files/*.md") if path.name != "index.md")
        self.assertEqual(len(module_files), 2)
        self.assertEqual(len(file_pages), 2)
        self.assertEqual(len(set(module_files)), 2)
        self.assertEqual(len(set(file_pages)), 2)
        self.assertTrue(all(":" not in name and "/" not in name and "\\" not in name and " " not in name for name in module_files))
        self.assertTrue(all(":" not in name and "/" not in name and "\\" not in name and " " not in name for name in file_pages))

        files_index_text = self.read_text(output_dir / "files" / "index.md")
        module_map_text = self.read_text(output_dir / "module-map.md")
        for name in module_files:
            self.assertIn(name, module_map_text)
        for name in file_pages:
            self.assertIn(name, files_index_text)

    def test_render_reports_missing_required_json(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        (analysis_dir / "inventory.json").write_text("{}", encoding="utf-8")

        output_dir = self.make_temp_dir() / "docs"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 2)
        self.assertIn("missing required artifact", render_result.stderr.lower())

    def test_render_reports_invalid_json(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        for artifact_name in REQUIRED_ANALYSIS_ARTIFACTS:
            payload = "{}"
            if artifact_name == "inventory.json":
                payload = "{invalid json"
            (analysis_dir / artifact_name).write_text(payload, encoding="utf-8")

        output_dir = self.make_temp_dir() / "docs"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 2)
        self.assertIn("invalid json in inventory.json", render_result.stderr.lower())

    def test_render_managed_cleanup_preserves_user_files_and_removes_old_generated_files(self) -> None:
        output_dir = self.make_temp_dir() / "docs"
        first_analysis_dir = self.make_temp_dir() / "analysis-first"
        second_analysis_dir = self.make_temp_dir() / "analysis-second"

        self.build_minimal_analysis(
            first_analysis_dir,
            overrides={
                "inventory.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((first_analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "files": [
                        {
                            "path": "src/example.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 12,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                        {
                            "path": "src/extra.py",
                            "extension": ".py",
                            "file_type": "source",
                            "size_bytes": 12,
                            "language": "python",
                            "is_test": False,
                            "is_config": False,
                            "is_possible_entrypoint": False,
                            "artifact_role": None,
                            "is_fixture": False,
                            "is_generated": False,
                            "is_packaging_metadata": False,
                            "analysis_depth": "deep",
                            "supports_deep_analysis": True,
                        },
                    ],
                },
                "function-index.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": str((first_analysis_dir / ".." / "project").resolve()),
                    "tool_version": "0.1.0",
                    "entities": [
                        {
                            "name": "build_message",
                            "entity_type": "function",
                            "type": "function",
                            "file": "src/example.py",
                            "parameters": [],
                            "confidence": "high",
                        },
                        {
                            "name": "extra",
                            "entity_type": "function",
                            "type": "function",
                            "file": "src/extra.py",
                            "parameters": [],
                            "confidence": "high",
                        },
                    ],
                },
            },
        )
        self.build_minimal_analysis(second_analysis_dir)

        first_render = self.run_render(first_analysis_dir, output_dir)
        self.assertEqual(first_render.returncode, 0, msg=first_render.stderr or first_render.stdout)

        user_file = output_dir / "user-notes.md"
        user_file.write_text("keep me", encoding="utf-8")

        first_manifest = self.read_json(output_dir / "doc-manifest.json")
        old_extra_page = next(path for path in first_manifest["generated_files"] if "extra" in path and path.startswith("files/"))
        self.assertTrue((output_dir / old_extra_page).exists())

        second_render = self.run_render(second_analysis_dir, output_dir)
        self.assertEqual(second_render.returncode, 0, msg=second_render.stderr or second_render.stdout)
        self.assertTrue(user_file.exists())
        self.assertFalse((output_dir / old_extra_page).exists())

    def test_render_works_without_source_files_after_analysis(self) -> None:
        temp_root = self.make_temp_dir()
        project_copy = temp_root / "fixture-copy"
        shutil.copytree(
            FIXTURE_PROJECT,
            project_copy,
            ignore=shutil.ignore_patterns(".docgen-analysis*", "docs-generated"),
        )

        analysis_dir = temp_root / "analysis"
        analyze_result = self.run_cli("analyze", str(project_copy), "--output", str(analysis_dir))
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)

        shutil.rmtree(project_copy, onerror=self.force_remove_readonly)

        output_dir = temp_root / "docs"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)
        self.assertTrue((output_dir / "doc-manifest.json").exists())

    def test_markdown_outputs_hide_is_internal_and_is_external(self) -> None:
        analyze_result, analysis_dir = self.run_analysis(FIXTURE_PROJECT)
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)

        output_dir = self.make_temp_dir() / "docs-generated"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        bad_files = []
        for path in output_dir.rglob("*.md"):
            text = self.read_text(path)
            if "is_internal" in text or "is_external" in text:
                bad_files.append(path)
        self.assertFalse(bad_files, bad_files)

    def test_production_only_analyze_and_render_smoke(self) -> None:
        analyze_result, analysis_dir = self.run_analysis(
            ROOT,
            "--include-tests=false",
            "--include-fixtures=false",
        )
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)

        output_dir = self.make_temp_dir() / "docs-production"
        render_result = self.run_render(analysis_dir, output_dir)
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        manifest = self.read_json(output_dir / "doc-manifest.json")
        self.assertTrue((output_dir / "files" / "index.md").exists())
        self.assertFalse(any(path.endswith("modules/directory-tests.md") for path in manifest["generated_files"]))


if __name__ == "__main__":
    unittest.main()
