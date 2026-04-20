from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.llm.explain_plan import build_explain_plan  # noqa: E402
from docgen.llm import prompts  # noqa: E402

FIXTURE_PROJECT = ROOT / "tests" / "fixtures" / "sample_project"


class ExplainPlanCliTests(unittest.TestCase):
    def run_cli(self, *args: str, extra_env: dict[str, str | None] | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        pythonpath = str(SRC_ROOT)
        if environment.get("PYTHONPATH"):
            pythonpath = f"{pythonpath}{os.pathsep}{environment['PYTHONPATH']}"
        environment["PYTHONPATH"] = pythonpath
        if extra_env:
            for key, value in extra_env.items():
                if value is None:
                    environment.pop(key, None)
                else:
                    environment[key] = value
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

    def pipeline(self, project_path: Path, *analysis_args: str) -> tuple[Path, Path]:
        temp_root = self.make_temp_dir()
        analysis_dir = temp_root / "analysis"
        docs_dir = temp_root / "docs"

        analyze_result = self.run_cli("analyze", str(project_path), "--output", str(analysis_dir), *analysis_args)
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)

        render_result = self.run_cli("render", "--analysis", str(analysis_dir), "--output", str(docs_dir))
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)
        return analysis_dir, docs_dir

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
            self.write_json(analysis_dir / artifact_name, payload)
        return analysis_dir

    def test_explain_plan_command_creates_plan_with_openrouter_defaults(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        output_path = docs_dir / "explain-plan.json"

        result = self.run_cli(
            "explain-plan",
            "--analysis",
            str(analysis_dir),
            "--docs",
            str(docs_dir),
            "--output",
            str(output_path),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("Explain plan saved to:", result.stdout)

        payload = self.read_json(output_path)
        self.assertEqual(payload["model_plan"]["provider"], "openrouter")
        self.assertEqual(payload["model_plan"]["default_model"], "google/gemma-4-26b-a4b-it")
        self.assertEqual(payload["model_plan"]["api_key_env"], "OPENROUTER_API")
        self.assertFalse(payload["model_plan"]["network_enabled_in_stage_3a"])
        self.assertEqual(payload["docs_manifest"]["documentation_layout_version"], "2.1")
        self.assertTrue(payload["modules"])

    def test_explain_plan_does_not_require_openrouter_api(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        output_path = docs_dir / "explain-plan.json"

        result = self.run_cli(
            "explain-plan",
            "--analysis",
            str(analysis_dir),
            "--docs",
            str(docs_dir),
            "--output",
            str(output_path),
            extra_env={"OPENROUTER_API": None},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertTrue(output_path.exists())

    def test_explain_plan_direct_builder_does_not_touch_network(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)

        def fail_network(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("network access is not allowed in Stage 3A")

        with mock.patch.object(socket, "create_connection", side_effect=fail_network):
            with mock.patch.object(socket, "socket", side_effect=fail_network):
                payload = build_explain_plan(analysis_dir, docs_dir)
        self.assertEqual(payload["model_plan"]["provider"], "openrouter")

    def test_explain_plan_warns_on_layout_version_mismatch(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        manifest_path = docs_dir / "doc-manifest.json"
        manifest = self.read_json(manifest_path)
        manifest["documentation_layout_version"] = "2.0"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        payload = build_explain_plan(analysis_dir, docs_dir)
        self.assertIn("expected documentation_layout_version 2.1", payload["warnings"])

    def test_explain_plan_live_assigns_full_and_summary_modes(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        payload = build_explain_plan(analysis_dir, docs_dir)
        modules = {module["name"]: module for module in payload["modules"]}

        self.assertEqual(modules["docgen"]["explain_mode"], "full")
        self.assertEqual(modules["docgen"]["priority"], "high")
        self.assertEqual(modules["docgen"]["module_page_role"], "detailed")
        self.assertEqual(modules["entry:cli"]["explain_mode"], "summary")
        self.assertEqual(modules["entry:cli"]["module_page_role"], "entrypoint")
        self.assertEqual(modules["src"]["explain_mode"], "summary")
        self.assertEqual(modules["src"]["module_page_role"], "aggregate")

    def test_explain_plan_fixture_modules_are_low_priority_and_not_empty(self) -> None:
        analysis_dir, docs_dir = self.pipeline(FIXTURE_PROJECT)
        payload = build_explain_plan(analysis_dir, docs_dir)

        self.assertTrue(payload["modules"])
        self.assertTrue(all(module["priority"] == "low" for module in payload["modules"]))
        self.assertTrue(all(module["explain_mode"] == "summary" for module in payload["modules"]))
        self.assertTrue(
            any("only test/fixture modules are available" in warning for warning in payload["warnings"])
            or any(
                any("only test/fixture modules are available" in warning for warning in module["warnings"])
                for module in payload["modules"]
            )
        )

    def test_explain_plan_includes_file_doc_refs_and_budget_fields(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        payload = build_explain_plan(analysis_dir, docs_dir)
        docgen_module = next(module for module in payload["modules"] if module["name"] == "docgen")

        self.assertTrue(docgen_module["module_doc_path"].startswith("modules/"))
        self.assertTrue(isinstance(docgen_module["module_doc_exists"], bool))
        self.assertTrue(docgen_module["file_doc_paths"])
        self.assertTrue(all("exists" in item and isinstance(item["exists"], bool) for item in docgen_module["file_doc_paths"]))
        self.assertIsInstance(docgen_module["estimated_input_tokens"], int)
        self.assertIsInstance(docgen_module["estimated_output_tokens"], int)
        self.assertIn(docgen_module["budget_status"], {"ok", "near_limit", "over_limit"})
        self.assertTrue(docgen_module["planned_output_path"].startswith("docs/enhanced/modules/"))

    def test_explain_plan_uses_manifest_file_page_mapping_when_available(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        manifest_path = docs_dir / "doc-manifest.json"
        manifest = self.read_json(manifest_path)
        analyzer_entry = next(
            entry for entry in manifest["file_pages"] if entry["source_file"] == "src/docgen/analyzer.py"
        )
        original_doc_path = analyzer_entry["doc_path"]
        custom_doc_path = "files/custom-analyzer.md"
        shutil.copyfile(docs_dir / original_doc_path, docs_dir / custom_doc_path)
        (docs_dir / original_doc_path).unlink()
        analyzer_entry["doc_path"] = custom_doc_path
        manifest["generated_files"] = sorted(
            custom_doc_path if path == original_doc_path else path
            for path in manifest["generated_files"]
        )
        self.write_json(manifest_path, manifest)

        payload = build_explain_plan(analysis_dir, docs_dir)
        docgen_module = next(module for module in payload["modules"] if module["name"] == "docgen")
        analyzer_ref = next(
            ref for ref in docgen_module["file_doc_paths"] if ref["source_file"] == "src/docgen/analyzer.py"
        )
        self.assertEqual(analyzer_ref["doc_path"], custom_doc_path)
        self.assertTrue(analyzer_ref["exists"])
        self.assertNotIn("file_doc_missing:src/docgen/analyzer.py", docgen_module["missing_context"])

    def test_explain_plan_detects_missing_file_doc_context(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        missing_doc = docs_dir / "files" / "file-src-docgen-analyzer-py.md"
        missing_doc.unlink()

        payload = build_explain_plan(analysis_dir, docs_dir)
        docgen_module = next(module for module in payload["modules"] if module["name"] == "docgen")
        missing_refs = [item for item in docgen_module["file_doc_paths"] if item["source_file"] == "src/docgen/analyzer.py"]
        self.assertTrue(missing_refs)
        self.assertFalse(missing_refs[0]["exists"])
        self.assertIn("file_doc_missing:src/docgen/analyzer.py", docgen_module["missing_context"])

    def test_explain_plan_reports_consistency_mismatch_when_manifest_lacks_required_file_page(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        manifest_path = docs_dir / "doc-manifest.json"
        manifest = self.read_json(manifest_path)
        manifest["file_pages"] = [
            entry for entry in manifest["file_pages"] if entry["source_file"] != "src/docgen/analyzer.py"
        ]
        self.write_json(manifest_path, manifest)

        payload = build_explain_plan(analysis_dir, docs_dir)
        self.assertIn("src/docgen/analyzer.py", payload["consistency"]["missing_file_pages"])
        self.assertTrue(
            any("src/docgen/analyzer.py" in item for item in payload["consistency"]["analysis_render_mismatches"])
        )
        docgen_module = next(module for module in payload["modules"] if module["name"] == "docgen")
        self.assertIn("file_doc_missing:src/docgen/analyzer.py", docgen_module["missing_context"])

    def test_explain_plan_falls_back_to_legacy_slug_logic_when_manifest_file_pages_are_missing(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        manifest_path = docs_dir / "doc-manifest.json"
        manifest = self.read_json(manifest_path)
        manifest.pop("file_pages", None)
        self.write_json(manifest_path, manifest)

        payload = build_explain_plan(analysis_dir, docs_dir)
        self.assertIn("manifest_file_pages_missing_fallback_to_slug", payload["warnings"])
        self.assertFalse(payload["consistency"]["manifest_has_file_pages"])
        docgen_module = next(module for module in payload["modules"] if module["name"] == "docgen")
        analyzer_ref = next(
            ref for ref in docgen_module["file_doc_paths"] if ref["source_file"] == "src/docgen/analyzer.py"
        )
        self.assertTrue(analyzer_ref["exists"])
        self.assertNotIn("file_doc_missing:src/docgen/analyzer.py", docgen_module["missing_context"])

    def test_explain_plan_does_not_require_file_page_for_inventory_only_file_without_facts(self) -> None:
        analysis_dir = self.make_temp_dir() / "analysis"
        project_path = str((analysis_dir / ".." / "project").resolve())
        self.build_minimal_analysis(
            analysis_dir,
            overrides={
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
                        },
                        {
                            "path": "src/idle.py",
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
                            "files": ["src/example.py", "src/idle.py"],
                            "source_files": ["src/example.py", "src/idle.py"],
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
                    "file_count": 2,
                    "source_file_count": 2,
                    "test_file_count": 0,
                    "config_file_count": 0,
                    "entity_count": 1,
                    "import_count": 1,
                    "deep_analyzed_file_count": 1,
                    "shallow_indexed_file_count": 1,
                    "detected_languages": ["python"],
                    "applied_analyzers": ["synthetic"],
                    "limitations": [],
                },
                "coverage-report.json": {
                    "schema_version": "1.0",
                    "generated_at": "2026-04-20T00:00:00+00:00",
                    "project_path": project_path,
                    "tool_version": "0.1.0",
                    "indexed_file_count": 2,
                    "deep_analyzed_file_count": 1,
                    "shallow_indexed_file_count": 1,
                    "unsupported_deep_extensions": [],
                    "supported_languages": ["python"],
                    "detected_supported_languages": ["python"],
                    "unresolved_import_count": 0,
                    "low_confidence_entity_count": 0,
                    "limitations": [],
                },
            },
        )

        docs_dir = self.make_temp_dir() / "docs"
        render_result = self.run_cli("render", "--analysis", str(analysis_dir), "--output", str(docs_dir))
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        payload = build_explain_plan(analysis_dir, docs_dir)
        module = next(module for module in payload["modules"] if module["name"] == "example")
        self.assertFalse(any(ref["source_file"] == "src/idle.py" for ref in module["file_doc_paths"]))
        self.assertNotIn("file_doc_missing:src/idle.py", module["missing_context"])

    def test_explain_plan_fresh_live_pipeline_has_no_llm_file_page_mismatches(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        payload = build_explain_plan(analysis_dir, docs_dir)

        self.assertEqual(payload["consistency"]["missing_file_pages"], [])
        self.assertEqual(payload["consistency"]["analysis_render_mismatches"], [])
        self.assertFalse(payload["consistency"]["stale_docs_detected"])

        llm_missing = []
        for module in payload["modules"]:
            for marker in module["missing_context"]:
                if "src/docgen/llm/" in marker:
                    llm_missing.append((module["name"], marker))
        self.assertFalse(llm_missing, llm_missing)

    def test_explain_plan_is_deterministic_for_modules_and_planned_paths(self) -> None:
        analysis_dir, docs_dir = self.pipeline(ROOT)
        first = build_explain_plan(analysis_dir, docs_dir)
        second = build_explain_plan(analysis_dir, docs_dir)

        first_pairs = [(module["name"], module["planned_output_path"]) for module in first["modules"]]
        second_pairs = [(module["name"], module["planned_output_path"]) for module in second["modules"]]
        self.assertEqual(first_pairs, second_pairs)

    def test_explain_plan_works_without_source_files_after_analyze_and_render(self) -> None:
        temp_root = self.make_temp_dir()
        project_copy = temp_root / "fixture-copy"
        shutil.copytree(
            FIXTURE_PROJECT,
            project_copy,
            ignore=shutil.ignore_patterns(".docgen-analysis*", "docs-generated"),
        )

        analysis_dir = temp_root / "analysis"
        docs_dir = temp_root / "docs"
        analyze_result = self.run_cli("analyze", str(project_copy), "--output", str(analysis_dir))
        self.assertEqual(analyze_result.returncode, 0, msg=analyze_result.stderr or analyze_result.stdout)
        render_result = self.run_cli("render", "--analysis", str(analysis_dir), "--output", str(docs_dir))
        self.assertEqual(render_result.returncode, 0, msg=render_result.stderr or render_result.stdout)

        shutil.rmtree(project_copy, onerror=self.force_remove_readonly)
        payload = build_explain_plan(analysis_dir, docs_dir)
        self.assertTrue(payload["modules"])

    def test_prompt_templates_include_required_sections_and_anti_hallucination_rules(self) -> None:
        self.assertIn("выдумывать бизнес-смысл", prompts.MODULE_EXPLANATION_SYSTEM_PROMPT)
        self.assertIn("утверждать runtime-поведение без фактов", prompts.MODULE_EXPLANATION_SYSTEM_PROMPT)
        self.assertIn("Фактическая опора", prompts.MODULE_EXPLANATION_OUTPUT_CONTRACT)
        self.assertIn("unsupported_claims", prompts.VERIFICATION_OUTPUT_CONTRACT)
        self.assertIn("recommended_fixes", prompts.VERIFICATION_OUTPUT_CONTRACT)


if __name__ == "__main__":
    unittest.main()
