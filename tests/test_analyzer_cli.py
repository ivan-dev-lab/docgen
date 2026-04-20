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
FIXTURE_PROJECT = ROOT / "tests" / "fixtures" / "sample_project"
ARTIFACT_NAMES = (
    "inventory.json",
    "function-index.json",
    "dependency-graph.json",
    "module-candidates.json",
    "analysis-summary.json",
    "artifact-manifest.json",
    "coverage-report.json",
)
GENERATED_ARTIFACT_NAMES = {
    "analysis-summary.json",
    "artifact-manifest.json",
    "coverage-report.json",
    "dependency-graph.json",
    "function-index.json",
    "inventory.json",
    "module-candidates.json",
}


class AnalyzerCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        pythonpath = str(ROOT / "src")
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

    def run_analysis(self, project_path: Path, *extra_args: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        output_dir = Path(temp_directory.name) / "analysis"
        result = self.run_cli("analyze", str(project_path), "--output", str(output_dir), *extra_args)
        return result, output_dir

    def load_artifacts(self, output_dir: Path) -> dict[str, dict]:
        artifacts = {}
        for artifact_name in ARTIFACT_NAMES:
            artifact_path = output_dir / artifact_name
            self.assertTrue(artifact_path.exists(), artifact_name)
            artifacts[artifact_name] = json.loads(artifact_path.read_text(encoding="utf-8"))
        return artifacts

    def assert_common_metadata(self, artifact: dict, expected_project_path: Path) -> None:
        self.assertEqual(artifact["schema_version"], "1.0")
        self.assertIn("generated_at", artifact)
        self.assertEqual(artifact["project_path"], str(expected_project_path.resolve()))
        self.assertEqual(artifact["tool_version"], "0.1.0")

    def assert_no_bad_inventory_paths(self, paths: list[str], *, allow_fixture_paths: bool) -> None:
        bad_markers = [
            ".docgen-analysis",
            ".docgen-analysis-live",
            "docgen-smoke",
            ".egg-info",
        ]
        for path in paths:
            self.assertFalse(any(marker in path for marker in bad_markers), path)
            self.assertFalse(path.endswith(".docgen-analysis.zip"), path)
            self.assertFalse(path.endswith(".docgen-analysis-live.zip"), path)
            if not allow_fixture_paths:
                self.assertFalse(path.startswith("tests/fixtures/sample_project/"), path)
            if Path(path).name in GENERATED_ARTIFACT_NAMES:
                self.fail(f"Generated analysis artifact leaked into inventory: {path}")

    def make_dirty_fixture_copy(self) -> Path:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        fixture_copy = Path(temp_directory.name) / "sample_project"
        shutil.copytree(FIXTURE_PROJECT, fixture_copy)

        (fixture_copy / ".docgen-analysis.zip").write_text("ignored zip", encoding="utf-8")
        (fixture_copy / ".docgen-analysis-live.zip").write_text("ignored zip", encoding="utf-8")

        live_dir = fixture_copy / ".docgen-analysis-live"
        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "analysis-summary.json").write_text("{}", encoding="utf-8")

        smoke_dir = fixture_copy / "docgen-smoke"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        (smoke_dir / "inventory.json").write_text("{}", encoding="utf-8")

        generated_json = fixture_copy / "dependency-graph.json"
        generated_json.write_text("{}", encoding="utf-8")

        egg_info_dir = fixture_copy / "src" / "docgen.egg-info"
        egg_info_dir.mkdir(parents=True, exist_ok=True)
        (egg_info_dir / "PKG-INFO").write_text("metadata", encoding="utf-8")
        (egg_info_dir / "SOURCES.txt").write_text("src/docgen/analyzer.py", encoding="utf-8")
        return fixture_copy

    def test_fixture_root_analysis_marks_fixture_assets_and_keeps_richer_metadata(self) -> None:
        result, output_dir = self.run_analysis(FIXTURE_PROJECT)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("Analysis artifacts saved to:", result.stdout)

        artifacts = self.load_artifacts(output_dir)
        for artifact in artifacts.values():
            self.assert_common_metadata(artifact, FIXTURE_PROJECT)

        inventory = artifacts["inventory.json"]
        files = {entry["path"]: entry for entry in inventory["files"]}
        self.assertIn("src/index.ts", files)
        self.assertIn("python_pkg/core.py", files)
        self.assertIn("tests/test_core.py", files)
        self.assertTrue(all(entry["is_fixture"] for entry in files.values()))
        self.assertTrue(all(entry["artifact_role"] == "fixture" for entry in files.values()))
        self.assert_no_bad_inventory_paths(list(files), allow_fixture_paths=True)

        function_index = artifacts["function-index.json"]
        entities = {(entry["file"], entry["name"], entry["entity_type"]): entry for entry in function_index["entities"]}
        build_message = entities[("python_pkg/core.py", "build_message", "function")]
        self.assertEqual(build_message["type"], "function")
        self.assertEqual(build_message["signature"], "def build_message(name: str) -> str")
        self.assertEqual(build_message["return_annotation"], "str")
        self.assertEqual(build_message["docstring"], "Build a user-facing greeting message.")

        bootstrap = entities[("src/index.ts", "bootstrapApp", "function")]
        self.assertEqual(bootstrap["type"], "function")
        self.assertEqual(bootstrap["signature"], "export function bootstrapApp(): number")
        self.assertEqual(bootstrap["return_annotation"], "number")

        dependency_graph = artifacts["dependency-graph.json"]
        import_edges = dependency_graph["imports"]
        self.assertTrue(any(edge["imported"] == "os" and edge["dependency_type"] == "stdlib" for edge in import_edges))
        self.assertTrue(any(edge["imported"] == "fs" and edge["dependency_type"] == "node_builtin" for edge in import_edges))
        self.assertTrue(any(edge["imported"] == "react" and edge["dependency_type"] == "third_party" for edge in import_edges))
        self.assertTrue(any(edge["imported"] == "./lib/math" and edge["dependency_type"] == "internal" for edge in import_edges))

        module_candidates = artifacts["module-candidates.json"]
        candidates = module_candidates["candidates"]
        self.assertTrue(any(candidate["type"] == "fixture" for candidate in candidates))
        self.assertTrue(any(candidate["type"] == "test_asset" for candidate in candidates))
        core_candidate = next(candidate for candidate in candidates if candidate["name"] == "core")
        self.assertEqual(core_candidate["type"], "test_asset")
        self.assertEqual(core_candidate["source_files"], ["python_pkg/core.py"])
        self.assertEqual(core_candidate["test_files"], ["tests/test_core.py"])
        self.assertTrue(
            any(
                relation["relation_type"] == "tests"
                and relation["source"] == "tests/test_core.py"
                and relation["target"] == "python_pkg/core.py"
                for relation in module_candidates["relations"]
            )
        )

        coverage_report = artifacts["coverage-report.json"]
        self.assertEqual(coverage_report["unresolved_import_count"], 0)
        self.assertGreaterEqual(coverage_report["deep_analyzed_file_count"], 8)

    def test_ignore_rules_exclude_generated_archives_smoke_dirs_and_packaging_metadata(self) -> None:
        fixture_copy = self.make_dirty_fixture_copy()
        output_dir = fixture_copy / "custom-output"
        result = self.run_cli("analyze", str(fixture_copy), "--output", str(output_dir))
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

        artifacts = self.load_artifacts(output_dir)
        inventory = artifacts["inventory.json"]
        paths = [entry["path"] for entry in inventory["files"]]
        self.assert_no_bad_inventory_paths(paths, allow_fixture_paths=True)
        self.assertFalse(any(path.endswith(".zip") for path in paths))
        self.assertFalse(any(path.endswith("PKG-INFO") for path in paths))
        self.assertFalse(any(path.endswith("SOURCES.txt") for path in paths))
        self.assertFalse(any(path.startswith("custom-output/") for path in paths))

    def test_live_default_excludes_fixture_generated_and_packaging_content(self) -> None:
        result, output_dir = self.run_analysis(ROOT)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

        artifacts = self.load_artifacts(output_dir)
        for artifact in artifacts.values():
            self.assert_common_metadata(artifact, ROOT)

        inventory = artifacts["inventory.json"]
        paths = [entry["path"] for entry in inventory["files"]]
        self.assert_no_bad_inventory_paths(paths, allow_fixture_paths=False)
        self.assertFalse(any(entry["is_fixture"] for entry in inventory["files"]))
        self.assertFalse(any(entry["is_generated"] for entry in inventory["files"]))
        self.assertFalse(any(entry["is_packaging_metadata"] for entry in inventory["files"]))

        module_candidates = artifacts["module-candidates.json"]
        self.assertFalse(any(candidate["type"] in {"fixture", "test_asset"} for candidate in module_candidates["candidates"]))

    def test_live_include_fixtures_marks_fixture_files_and_fixture_candidates(self) -> None:
        result, output_dir = self.run_analysis(ROOT, "--include-fixtures=true")
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

        artifacts = self.load_artifacts(output_dir)
        inventory = artifacts["inventory.json"]
        fixture_entries = [entry for entry in inventory["files"] if entry["path"].startswith("tests/fixtures/sample_project/")]
        self.assertTrue(fixture_entries)
        self.assertTrue(all(entry["is_fixture"] for entry in fixture_entries))
        self.assertTrue(all(entry["artifact_role"] == "fixture" for entry in fixture_entries))

        module_candidates = artifacts["module-candidates.json"]
        fixture_candidates = [
            candidate
            for candidate in module_candidates["candidates"]
            if any(file_path.startswith("tests/fixtures/sample_project/") for file_path in candidate["files"])
        ]
        self.assertTrue(fixture_candidates)
        self.assertTrue(all(candidate["type"] in {"fixture", "test_asset"} for candidate in fixture_candidates))

    def test_analyze_command_reports_missing_project_path(self) -> None:
        missing_path = ROOT / "tests" / "fixtures" / "missing_project"
        result = self.run_cli("analyze", str(missing_path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Project path does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
