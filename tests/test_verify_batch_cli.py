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
from docgen.llm.module_batch_verifier import verify_batch, verification_batch_manifest_path  # noqa: E402
from docgen.llm.run_history import update_history_index  # noqa: E402


class VerifyBatchCliTests(unittest.TestCase):
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

    def build_fixture(self, root: Path, *, missing_enhanced: set[str] | None = None) -> tuple[Path, Path, Path]:
        missing_enhanced = missing_enhanced or set()
        docs_dir = root / "docs" / "generated"
        enhanced_root = root / "docs" / "enhanced"
        verification_root = enhanced_root / "verification"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "modules").mkdir(exist_ok=True)
        (docs_dir / "files").mkdir(exist_ok=True)
        (docs_dir / "functions").mkdir(exist_ok=True)
        for name in ("zeta", "alpha", "beta", "skipme"):
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
            make_module("skipme", priority="low", explain_mode="skip", role="api"),
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

        for module in modules:
            name = module["name"]
            if name in missing_enhanced:
                continue
            enhanced_path = enhanced_root / module["module_doc_path"]
            enhanced_path.parent.mkdir(parents=True, exist_ok=True)
            enhanced_path.write_text(build_enhanced_markdown(name), encoding="utf-8")
            self.write_json(
                enhanced_root / "llm-runs" / f"{enhanced_path.stem}.metadata.json",
                {
                    "schema_version": "1.0",
                    "module": name,
                    "module_doc_path": module["module_doc_path"],
                    "context_fingerprint": f"metadata-fingerprint-{name}",
                    "retained_context_files": [module["module_doc_path"]],
                    "truncated_context_files": [],
                    "context_reduced": False,
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        return plan_path, enhanced_root, verification_root

    def test_verify_batch_dry_run_does_not_require_key_or_network_or_write_current_manifest(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root, missing_enhanced={"beta"})

        def fail_network(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("network access is not allowed in dry-run")

        with mock.patch.object(socket, "create_connection", side_effect=fail_network):
            with mock.patch.object(socket, "socket", side_effect=fail_network):
                exit_code, stdout, stderr = self.capture_main(
                    [
                        "verify-batch",
                        "--plan",
                        str(plan_path),
                        "--enhanced",
                        str(enhanced_root),
                        "--output",
                        str(verification_root),
                        "--dry-run",
                    ],
                    env={},
                    cwd=root,
                )

        self.assertEqual(exit_code, 0, msg=stderr)
        manifest = json.loads(stdout)
        self.assertFalse(manifest["network_call"])
        self.assertEqual(manifest["skipped_missing_enhanced_count"], 1)
        self.assertEqual(manifest["dry_run_planned_count"], 3)
        self.assertFalse(verification_batch_manifest_path(verification_root).exists())
        self.assertFalse((verification_root / "module-package-zeta.verification.json").exists())

    def test_verify_batch_selection_only_module_limit_sorting_and_same_context(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root)

        manifest = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True)
        self.assertEqual(manifest["selected_modules"], ["zeta", "alpha", "beta", "skipme"])
        self.assertEqual([r["module"] for r in manifest["results"]], ["zeta", "alpha", "beta", "skipme"])
        self.assertTrue(all(r["verification_mode"] == "same_context" for r in manifest["results"]))
        self.assertTrue(all(r["context_source"] == "generation_metadata" for r in manifest["results"]))

        only = verify_batch(
            plan_path,
            enhanced_root,
            verification_root,
            dry_run=True,
            only_modules=["beta", "zeta"],
            limit=1,
        )
        self.assertEqual(only["selected_modules"], ["zeta"])
        self.assertEqual(only["total_modules_selected"], 1)

    def test_verify_batch_module_not_found_reports_available_modules(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root)

        exit_code, stdout, stderr = self.capture_main(
            [
                "verify-batch",
                "--plan",
                str(plan_path),
                "--enhanced",
                str(enhanced_root),
                "--output",
                str(verification_root),
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

    def test_verify_batch_cache_hit_force_no_cache_and_broken_report(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root)
        first = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True, only_modules=["zeta"])
        result = first["results"][0]
        report_path = Path(result["verification_json_path"])
        summary_path = Path(result["verification_summary_path"])
        self.write_json(
            report_path,
            {
                "schema_version": "1.0",
                "module": "zeta",
                "verification_cache_key": result["verification_cache_key"],
                "verifier_status": "ok",
                "structured_output_valid": True,
                "verdict": "pass",
            },
        )
        summary_path.write_text("# summary\n", encoding="utf-8")

        cached = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True, only_modules=["zeta"])
        self.assertEqual(cached["results"][0]["status"], "skipped_cached")
        forced = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True, only_modules=["zeta"], force=True)
        self.assertEqual(forced["results"][0]["status"], "dry_run_planned")
        no_cache = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True, only_modules=["zeta"], no_cache=True)
        self.assertEqual(no_cache["results"][0]["status"], "dry_run_planned")

        report_path.write_text("{broken", encoding="utf-8")
        broken = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True, only_modules=["zeta"])
        self.assertEqual(broken["results"][0]["status"], "dry_run_planned")
        report_path.write_text(json.dumps({"verification_cache_key": result["verification_cache_key"]}), encoding="utf-8")
        summary_path.unlink()
        missing_summary = verify_batch(plan_path, enhanced_root, verification_root, dry_run=True, only_modules=["zeta"])
        self.assertEqual(missing_summary["results"][0]["status"], "dry_run_planned")

    def test_verify_batch_fake_verification_counts_usage_and_continues_after_failures(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root)
        calls: list[tuple[str, bool, str]] = []

        def fake_verify_module(plan_path_arg, module_name, enhanced_path_arg, output_path_arg, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((module_name, bool(kwargs.get("dry_run")), str(kwargs.get("verification_mode"))))
            if module_name == "zeta":
                raise ValueError("preflight failed")
            if kwargs.get("dry_run"):
                return {
                    "module": module_name,
                    "verification_mode": kwargs.get("verification_mode"),
                    "context_source": "generation_metadata",
                    "context_fingerprint": f"fingerprint-{module_name}",
                    "estimated_input_tokens": 100,
                    "enhanced_markdown_path": Path(enhanced_path_arg).as_posix(),
                    "network_call": False,
                }
            if module_name == "beta":
                return {
                    "module": module_name,
                    "verifier_status": "empty_content",
                    "structured_output_valid": False,
                    "verdict": "fail",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                }
            return {
                "module": module_name,
                "verifier_status": "ok",
                "structured_output_valid": True,
                "verdict": "warning",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        manifest = verify_batch(
            plan_path,
            enhanced_root,
            verification_root,
            force=True,
            only_modules=["zeta", "alpha", "beta"],
            verify_module_func=fake_verify_module,
        )

        self.assertEqual(manifest["failed_preflight_count"], 1)
        self.assertEqual(manifest["failed_verification_count"], 1)
        self.assertEqual(manifest["verified_count"], 1)
        self.assertEqual(manifest["warning_count"], 1)
        self.assertEqual(manifest["usage_totals"], {"prompt_tokens": 15, "completion_tokens": 3, "total_tokens": 18})
        statuses = {result["module"]: result["status"] for result in manifest["results"]}
        self.assertEqual(statuses["zeta"], "failed_preflight")
        self.assertEqual(statuses["alpha"], "verified_warning")
        self.assertEqual(statuses["beta"], "failed_verification")
        self.assertIn(("alpha", True, "same_context"), calls)
        self.assertIn(("alpha", False, "same_context"), calls)

    def test_verify_batch_live_records_current_history_index_and_ops_summary(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root)

        def fake_verify_module(plan_path_arg, module_name, enhanced_path_arg, output_path_arg, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("dry_run"):
                return {
                    "module": module_name,
                    "verification_mode": kwargs.get("verification_mode"),
                    "context_source": "generation_metadata",
                    "context_fingerprint": f"fingerprint-{module_name}",
                    "estimated_input_tokens": 100,
                    "enhanced_markdown_path": Path(enhanced_path_arg).as_posix(),
                    "network_call": False,
                }
            return {
                "module": module_name,
                "verifier_status": "ok",
                "structured_output_valid": True,
                "verdict": "pass",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        manifest = verify_batch(
            plan_path,
            enhanced_root,
            verification_root,
            force=True,
            only_modules=["alpha", "beta"],
            verify_module_func=fake_verify_module,
        )

        current_path = verification_batch_manifest_path(verification_root)
        current = json.loads(current_path.read_text(encoding="utf-8"))
        self.assertEqual(current["run_id"], manifest["run_id"])
        self.assertTrue(current["latest_live_run"])
        self.assertIn("history_manifest_path", current)
        self.assertIn("verified_count", current)
        self.assertIn("results", current)

        history_path = root / current["history_manifest_path"]
        self.assertTrue(history_path.is_file())
        history = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(history["run_id"], current["run_id"])

        index_path = enhanced_root / "history" / "verification" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["kind"], "verification")
        self.assertEqual(index["runs"][0]["run_id"], current["run_id"])
        self.assertFalse(Path(index["runs"][0]["manifest_path"]).is_absolute())
        self.assertTrue((root / index["runs"][0]["manifest_path"]).is_file())

        ops_json = json.loads((enhanced_root / "ops-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(ops_json["latest_verification_run_id"], current["run_id"])
        self.assertEqual(ops_json["verification_history_count"], 1)
        self.assertEqual(ops_json["latest_verification_cache_hit_rate"], 0.0)
        self.assertTrue((enhanced_root / "ops-summary.md").is_file())

    def test_verify_batch_history_newest_first_dedup_and_dry_run_preserves_current(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_root, verification_root = self.build_fixture(root)

        def fake_verify_module(plan_path_arg, module_name, enhanced_path_arg, output_path_arg, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("dry_run"):
                return {
                    "module": module_name,
                    "verification_mode": kwargs.get("verification_mode"),
                    "context_source": "generation_metadata",
                    "context_fingerprint": f"fingerprint-{module_name}",
                    "estimated_input_tokens": 100,
                    "enhanced_markdown_path": Path(enhanced_path_arg).as_posix(),
                    "network_call": False,
                }
            return {
                "module": module_name,
                "verifier_status": "ok",
                "structured_output_valid": True,
                "verdict": "warning",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        first = verify_batch(
            plan_path,
            enhanced_root,
            verification_root,
            force=True,
            only_modules=["alpha"],
            verify_module_func=fake_verify_module,
        )
        second = verify_batch(
            plan_path,
            enhanced_root,
            verification_root,
            force=True,
            only_modules=["beta"],
            verify_module_func=fake_verify_module,
        )
        index_path = enhanced_root / "history" / "verification" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["runs"][0]["run_id"], second["run_id"])
        self.assertEqual(index["runs"][1]["run_id"], first["run_id"])

        update_history_index(enhanced_root, "verification", second, second["history_manifest_path"])
        deduped = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [run["run_id"] for run in deduped["runs"]].count(second["run_id"]),
            1,
        )

        current_text = verification_batch_manifest_path(verification_root).read_text(encoding="utf-8")
        history_count = len(deduped["runs"])
        dry_run_manifest = verify_batch(
            plan_path,
            enhanced_root,
            verification_root,
            dry_run=True,
            only_modules=["alpha"],
            verify_module_func=fake_verify_module,
        )
        self.assertNotIn("run_id", dry_run_manifest)
        self.assertEqual(verification_batch_manifest_path(verification_root).read_text(encoding="utf-8"), current_text)
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


def build_enhanced_markdown(name: str) -> str:
    return f"""# Модуль: {name}

## Что известно
Факты: module {name}.

## Назначение
Не удалось определить по предоставленным фактам.

## Как работает
Не удалось определить по предоставленным фактам.

## Контур взаимодействия
Факты ограничены.

## Ключевые функции
Не удалось определить по предоставленным фактам.

## Зависимости
Факты ограничены.

## Что не удалось определить
Runtime-поведение.

## Уровень уверенности
Средний.

## Фактическая опора
- modules/module-package-{name}.md
"""


if __name__ == "__main__":
    unittest.main()
