from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.cli import main  # noqa: E402
from docgen.llm.module_batch_explainer import batch_manifest_path, explain_batch  # noqa: E402
from docgen.llm.run_history import update_history_index  # noqa: E402


class ExplainBatchCliTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        return Path(temp_directory.name)

    def capture_main(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        current_dir = Path.cwd()
        try:
            if cwd is not None:
                os.chdir(cwd)
            with mock.patch.dict(os.environ, env or {}, clear=True):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(argv)
        finally:
            os.chdir(current_dir)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def build_fixture(self, root: Path) -> tuple[Path, Path]:
        docs_dir = root / "docs" / "generated"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "modules").mkdir(exist_ok=True)
        (docs_dir / "files").mkdir(exist_ok=True)
        (docs_dir / "functions").mkdir(exist_ok=True)
        for name in ("zeta", "alpha", "beta", "low", "skipme"):
            (docs_dir / "modules" / f"module-package-{name}.md").write_text(
                f"# Module {name}\n\nentity_count: 1\n",
                encoding="utf-8",
            )
        (docs_dir / "coverage-report.md").write_text("# Coverage\nall modules\n", encoding="utf-8")
        (docs_dir / "dependency-map.md").write_text("# Dependencies\nzeta -> alpha\n", encoding="utf-8")
        (docs_dir / "module-map.md").write_text("# Module map\n", encoding="utf-8")
        (docs_dir / "files" / "index.md").write_text("# File index\n", encoding="utf-8")
        (docs_dir / "functions" / "function-index.md").write_text("# Function index\n", encoding="utf-8")

        modules = [
            make_module("zeta", priority="high", explain_mode="full", role="api"),
            make_module("alpha", priority="high", explain_mode="summary", role="api"),
            make_module("beta", priority="medium", explain_mode="full", role="api"),
            make_module("low", priority="low", explain_mode="full", role="api"),
            make_module("skipme", priority="high", explain_mode="skip", role="api"),
        ]
        plan = {
            "schema_version": "1.0",
            "docs_path": "docs/generated",
            "model_plan": {
                "provider": "openrouter",
                "default_model": "google/gemma-4-26b-a4b-it",
                "reasoning_enabled": True,
            },
            "budget_policy": {
                "max_input_tokens_per_module": 24000,
                "max_output_tokens_per_module": 4000,
            },
            "context_policy": {"include_function_index": "summary_or_links_only"},
            "global_docs": {
                "coverage_report": {"path": "coverage-report.md", "exists": True},
                "dependency_map": {"path": "dependency-map.md", "exists": True},
                "module_map": {"path": "module-map.md", "exists": True},
                "file_index": {"path": "files/index.md", "exists": True},
                "function_index": {"path": "functions/function-index.md", "exists": True},
            },
            "modules": modules,
            "output_contract": {"module_explanation_required_sections": []},
            "verification_policy": {},
        }
        plan_path = docs_dir / "explain-plan.json"
        self.write_json(plan_path, plan)
        return plan_path, root / "docs" / "enhanced"

    def test_explain_batch_dry_run_does_not_require_key_or_network_or_write_current_manifest(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)

        def fail_network(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("network access is not allowed in dry-run")

        with mock.patch.object(socket, "create_connection", side_effect=fail_network):
            with mock.patch.object(socket, "socket", side_effect=fail_network):
                exit_code, stdout, stderr = self.capture_main(
                    ["explain-batch", "--plan", str(plan_path), "--output", str(output_path), "--dry-run"],
                    env={},
                    cwd=root,
                )
        self.assertEqual(exit_code, 0, msg=stderr)
        manifest = json.loads(stdout)
        self.assertFalse(manifest["network_call"])
        self.assertFalse(batch_manifest_path(output_path).exists())
        self.assertEqual(manifest["skipped_by_plan_count"], 1)
        self.assertEqual(manifest["dry_run_planned_count"], 4)
        self.assertFalse((output_path / "modules" / "module-package-zeta.md").exists())

    def test_explain_batch_include_skip_only_module_limit_and_sorting(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)

        default_manifest = explain_batch(plan_path, output_path, dry_run=True)
        self.assertEqual(default_manifest["selected_modules"], ["zeta", "alpha", "beta", "low"])
        self.assertEqual([r["module"] for r in default_manifest["results"]], ["zeta", "alpha", "skipme", "beta", "low"])

        include_skip_manifest = explain_batch(plan_path, output_path, dry_run=True, include_skip=True)
        self.assertIn("skipme", include_skip_manifest["selected_modules"])
        self.assertEqual(include_skip_manifest["skipped_by_plan_count"], 0)

        only_manifest = explain_batch(
            plan_path,
            output_path,
            dry_run=True,
            only_modules=["beta", "zeta"],
            limit=1,
        )
        self.assertEqual(only_manifest["selected_modules"], ["zeta"])
        self.assertEqual(only_manifest["total_modules_selected"], 1)

    def test_explain_batch_module_not_found_reports_available_modules(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)

        exit_code, stdout, stderr = self.capture_main(
            [
                "explain-batch",
                "--plan",
                str(plan_path),
                "--output",
                str(output_path),
                "--only-module",
                "missing",
                "--dry-run",
            ],
            env={},
            cwd=root,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Available modules", stderr)

    def test_explain_batch_cache_hit_force_no_cache_and_broken_metadata(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)
        first = explain_batch(plan_path, output_path, dry_run=True, only_modules=["zeta"])
        result = first["results"][0]
        markdown_path = Path(result["output_path"])
        metadata_path = Path(result["metadata_path"])
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text("# cached\n", encoding="utf-8")
        self.write_json(
            metadata_path,
            {
                "schema_version": "1.0",
                "module": "zeta",
                "output_path": markdown_path.as_posix(),
                "cache_key": result["cache_key"],
                "generation_status": "success",
            },
        )

        cached = explain_batch(plan_path, output_path, dry_run=True, only_modules=["zeta"])
        self.assertEqual(cached["results"][0]["status"], "skipped_cached")
        forced = explain_batch(plan_path, output_path, dry_run=True, only_modules=["zeta"], force=True)
        self.assertEqual(forced["results"][0]["status"], "dry_run_planned")
        no_cache = explain_batch(plan_path, output_path, dry_run=True, only_modules=["zeta"], no_cache=True)
        self.assertEqual(no_cache["results"][0]["status"], "dry_run_planned")

        metadata_path.write_text("{broken", encoding="utf-8")
        broken = explain_batch(plan_path, output_path, dry_run=True, only_modules=["zeta"])
        self.assertEqual(broken["results"][0]["status"], "dry_run_planned")

    def test_explain_batch_fake_generation_counts_usage_and_continues_after_failure(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)
        calls: list[tuple[str, bool]] = []

        def fake_explain_module(plan_path_arg, module_name, output_path_arg, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((module_name, bool(kwargs.get("dry_run"))))
            if module_name == "zeta":
                raise ValueError("preflight failed")
            if kwargs.get("dry_run"):
                return {
                    "module": module_name,
                    "context_fingerprint": f"fingerprint-{module_name}",
                    "estimated_input_tokens": 100,
                }
            return {
                "module": module_name,
                "output_path": Path(output_path_arg).as_posix(),
                "metadata_path": (Path(output_path_arg).parent.parent / "llm-runs" / f"{Path(output_path_arg).stem}.metadata.json").as_posix(),
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        manifest = explain_batch(
            plan_path,
            output_path,
            force=True,
            only_modules=["zeta", "alpha", "beta"],
            explain_module_func=fake_explain_module,
        )

        self.assertEqual(manifest["failed_preflight_count"], 1)
        self.assertEqual(manifest["generated_count"], 2)
        self.assertEqual(manifest["failed_count"], 1)
        self.assertEqual(manifest["usage_totals"], {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24})
        statuses = {result["module"]: result["status"] for result in manifest["results"]}
        self.assertEqual(statuses["zeta"], "failed_preflight")
        self.assertEqual(statuses["alpha"], "generated")
        self.assertEqual(statuses["beta"], "generated")
        self.assertIn(("alpha", True), calls)
        self.assertIn(("alpha", False), calls)

    def test_explain_batch_live_records_current_history_index_and_ops_summary(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)

        def fake_explain_module(plan_path_arg, module_name, output_path_arg, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("dry_run"):
                return {
                    "module": module_name,
                    "context_fingerprint": f"fingerprint-{module_name}",
                    "estimated_input_tokens": 100,
                }
            return {
                "module": module_name,
                "output_path": Path(output_path_arg).as_posix(),
                "metadata_path": (
                    Path(output_path_arg).parent.parent / "llm-runs" / f"{Path(output_path_arg).stem}.metadata.json"
                ).as_posix(),
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        manifest = explain_batch(
            plan_path,
            output_path,
            force=True,
            only_modules=["alpha", "beta"],
            explain_module_func=fake_explain_module,
        )

        current_path = batch_manifest_path(output_path)
        current = json.loads(current_path.read_text(encoding="utf-8"))
        self.assertEqual(current["run_id"], manifest["run_id"])
        self.assertTrue(current["latest_live_run"])
        self.assertIn("history_manifest_path", current)
        self.assertIn("generated_count", current)
        self.assertIn("results", current)

        history_path = root / current["history_manifest_path"]
        self.assertTrue(history_path.is_file())
        history = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(history["run_id"], current["run_id"])

        index_path = output_path / "history" / "generation" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["kind"], "generation")
        self.assertEqual(index["runs"][0]["run_id"], current["run_id"])
        self.assertFalse(Path(index["runs"][0]["manifest_path"]).is_absolute())
        self.assertTrue((root / index["runs"][0]["manifest_path"]).is_file())

        ops_json = json.loads((output_path / "ops-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(ops_json["latest_generation_run_id"], current["run_id"])
        self.assertEqual(ops_json["generation_history_count"], 1)
        self.assertEqual(ops_json["latest_generation_cache_hit_rate"], 0.0)
        self.assertTrue((output_path / "ops-summary.md").is_file())

    def test_explain_batch_history_newest_first_dedup_and_dry_run_preserves_current(self) -> None:
        root = self.make_temp_dir()
        plan_path, output_path = self.build_fixture(root)

        def fake_explain_module(plan_path_arg, module_name, output_path_arg, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("dry_run"):
                return {
                    "module": module_name,
                    "context_fingerprint": f"fingerprint-{module_name}",
                    "estimated_input_tokens": 100,
                }
            return {
                "module": module_name,
                "output_path": Path(output_path_arg).as_posix(),
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        first = explain_batch(
            plan_path,
            output_path,
            force=True,
            only_modules=["alpha"],
            explain_module_func=fake_explain_module,
        )
        second = explain_batch(
            plan_path,
            output_path,
            force=True,
            only_modules=["beta"],
            explain_module_func=fake_explain_module,
        )
        index_path = output_path / "history" / "generation" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["runs"][0]["run_id"], second["run_id"])
        self.assertEqual(index["runs"][1]["run_id"], first["run_id"])

        update_history_index(output_path, "generation", second, second["history_manifest_path"])
        deduped = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [run["run_id"] for run in deduped["runs"]].count(second["run_id"]),
            1,
        )

        current_text = batch_manifest_path(output_path).read_text(encoding="utf-8")
        history_count = len(deduped["runs"])
        dry_run_manifest = explain_batch(
            plan_path,
            output_path,
            dry_run=True,
            only_modules=["alpha"],
            explain_module_func=fake_explain_module,
        )
        self.assertNotIn("run_id", dry_run_manifest)
        self.assertEqual(batch_manifest_path(output_path).read_text(encoding="utf-8"), current_text)
        after_dry_run_index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(len(after_dry_run_index["runs"]), history_count)


def make_module(name: str, *, priority: str, explain_mode: str, role: str) -> dict:
    return {
        "name": name,
        "type": "package",
        "module_page_role": role,
        "module_doc_path": f"modules/module-package-{name}.md",
        "module_doc_exists": True,
        "source_files": [],
        "test_files": [],
        "file_doc_paths": [],
        "entity_count": 1,
        "dependency_count": 0,
        "priority": priority,
        "explain_mode": explain_mode,
    }


if __name__ == "__main__":
    unittest.main()
