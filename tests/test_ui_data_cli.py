from __future__ import annotations

import io
import json
import os
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
from docgen.ui_data import build_ui_data  # noqa: E402


class UiDataCliTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        return Path(temp_directory.name)

    def capture_main(
        self,
        argv: list[str],
        *,
        cwd: Path,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        current_dir = Path.cwd()
        try:
            os.chdir(cwd)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(argv)
        finally:
            os.chdir(current_dir)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def build_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        generated = root / "docs" / "generated"
        enhanced = root / "docs" / "enhanced"
        output = root / "docs" / "ui-data"
        generated.mkdir(parents=True, exist_ok=True)
        modules_dir = generated / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        for name in ("alpha", "beta", "missing"):
            (modules_dir / f"module-package-{name}.md").write_text(
                f"# {name}\n\nThis markdown must not be parsed for UI semantics.\n",
                encoding="utf-8",
            )

        modules = [
            make_module("alpha", priority="high", explain_mode="full"),
            make_module("beta", priority="medium", explain_mode="summary"),
            make_module("missing", priority="low", explain_mode="full"),
        ]
        self.write_json(
            generated / "explain-plan.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:00:00+00:00",
                "modules": modules,
            },
        )
        self.write_json(
            generated / "doc-manifest.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:00:01+00:00",
                "generated_files": [f"modules/module-package-{name}.md" for name in ("alpha", "beta", "missing")],
            },
        )

        for name in ("alpha", "beta"):
            enhanced_path = enhanced / "modules" / f"module-package-{name}.md"
            enhanced_path.parent.mkdir(parents=True, exist_ok=True)
            enhanced_path.write_text(
                "# Enhanced\n\nverdict: pass\nunsupported_claims: 999\n",
                encoding="utf-8",
            )
            self.write_json(
                enhanced / "llm-runs" / f"module-package-{name}.metadata.json",
                {
                    "schema_version": "1.0",
                    "module": name,
                    "module_doc_path": f"modules/module-package-{name}.md",
                    "output_path": f"docs/enhanced/modules/module-package-{name}.md",
                    "metadata_path": f"docs/enhanced/llm-runs/module-package-{name}.metadata.json",
                    "generation_status": "success",
                    "context_fingerprint": f"context-{name}",
                },
            )

        self.write_json(
            enhanced / "llm-batch-run-manifest.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:01:00+00:00",
                "run_id": "generation-run",
                "history_manifest_path": "docs/enhanced/history/generation/generation-run.json",
                "generated_count": 1,
                "skipped_cached_count": 1,
                "skipped_by_plan_count": 0,
                "failed_count": 0,
                "usage_totals": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "results": [
                    {
                        "module": "alpha",
                        "status": "generated",
                        "output_path": "docs/enhanced/modules/module-package-alpha.md",
                        "metadata_path": "docs/enhanced/llm-runs/module-package-alpha.metadata.json",
                        "context_fingerprint": "context-alpha",
                    },
                    {
                        "module": "beta",
                        "status": "skipped_cached",
                        "output_path": "docs/enhanced/modules/module-package-beta.md",
                        "metadata_path": "docs/enhanced/llm-runs/module-package-beta.metadata.json",
                        "context_fingerprint": "context-beta",
                    },
                ],
            },
        )
        self.write_json(
            enhanced / "history" / "generation" / "index.json",
            {
                "schema_version": "1.0",
                "updated_at": "2026-04-28T00:01:01+00:00",
                "kind": "generation",
                "runs": [
                    {
                        "run_id": "generation-run",
                        "generated_at": "2026-04-28T00:01:00+00:00",
                        "manifest_path": "docs/enhanced/history/generation/generation-run.json",
                        "dry_run": False,
                        "provider": "openrouter",
                        "model": "model-a",
                        "selected_modules": ["alpha", "beta"],
                        "generated_count": 1,
                        "skipped_cached_count": 1,
                        "skipped_by_plan_count": 0,
                        "failed_count": 0,
                        "usage_totals": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                    }
                ],
            },
        )

        verification_root = enhanced / "verification"
        for name, verdict in (("alpha", "warning"), ("beta", "pass")):
            self.write_json(
                verification_root / f"module-package-{name}.verification.json",
                {
                    "schema_version": "1.0",
                    "module": name,
                    "verifier_status": "ok",
                    "structured_output_valid": True,
                    "verdict": verdict,
                    "unsupported_claims": [],
                    "weak_claims": [{}] if verdict == "warning" else [],
                    "missing_uncertainty": [],
                    "missing_factual_support": [],
                },
            )
            (verification_root / f"module-package-{name}.verification.md").write_text(
                "# Human summary\n\nverdict: fail\nweak_claims: 999\n",
                encoding="utf-8",
            )
        self.write_json(
            verification_root / "llm-batch-verification-manifest.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:02:00+00:00",
                "run_id": "verification-run",
                "history_manifest_path": "docs/enhanced/history/verification/verification-run.json",
                "verification_mode": "same_context",
                "verified_count": 2,
                "warning_count": 1,
                "failed_count": 0,
                "skipped_cached_count": 0,
                "skipped_missing_enhanced_count": 1,
                "usage_totals": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
                "results": [
                    {
                        "module": "alpha",
                        "status": "verified_warning",
                        "verification_json_path": "docs/enhanced/verification/module-package-alpha.verification.json",
                        "verification_summary_path": "docs/enhanced/verification/module-package-alpha.verification.md",
                        "verifier_status": "ok",
                        "structured_output_valid": True,
                        "verdict": "warning",
                    },
                    {
                        "module": "beta",
                        "status": "verified_pass",
                        "verification_json_path": "docs/enhanced/verification/module-package-beta.verification.json",
                        "verification_summary_path": "docs/enhanced/verification/module-package-beta.verification.md",
                        "verifier_status": "ok",
                        "structured_output_valid": True,
                        "verdict": "pass",
                    },
                ],
            },
        )
        self.write_json(
            enhanced / "history" / "verification" / "index.json",
            {
                "schema_version": "1.0",
                "updated_at": "2026-04-28T00:02:01+00:00",
                "kind": "verification",
                "runs": [
                    {
                        "run_id": "verification-run",
                        "generated_at": "2026-04-28T00:02:00+00:00",
                        "manifest_path": "docs/enhanced/history/verification/verification-run.json",
                        "dry_run": False,
                        "provider": "openrouter",
                        "model": "model-a",
                        "selected_modules": ["alpha", "beta"],
                        "verified_count": 2,
                        "warning_count": 1,
                        "failed_count": 0,
                        "skipped_cached_count": 0,
                        "skipped_missing_enhanced_count": 1,
                        "usage_totals": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
                    }
                ],
            },
        )
        self.write_json(
            enhanced / "ops-summary.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:02:02+00:00",
                "latest_generation_run_id": "generation-run",
                "latest_verification_run_id": "verification-run",
                "generation_history_count": 1,
                "verification_history_count": 1,
                "latest_generation_cache_hit_rate": 0.5,
                "latest_verification_cache_hit_rate": 0.0,
                "warnings": [],
            },
        )
        return generated, enhanced, output

    def test_build_ui_data_creates_contract_files_and_current_state(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)

        exit_code, stdout, stderr = self.capture_main(
            ["build-ui-data", "--generated", str(generated), "--enhanced", str(enhanced), "--output", str(output)],
            cwd=root,
        )

        self.assertEqual(exit_code, 0, msg=stderr)
        summary = json.loads(stdout)
        self.assertFalse(summary["network_call"])
        for name in ("current-state.json", "modules-index.json", "history-index.json", "ui-data-manifest.json"):
            self.assertTrue((output / name).is_file(), msg=name)

        current = json.loads((output / "current-state.json").read_text(encoding="utf-8"))
        self.assertEqual(current["latest_generation_run"]["run_id"], "generation-run")
        self.assertEqual(current["latest_verification_run"]["run_id"], "verification-run")
        self.assertEqual(current["module_counts"]["total_modules"], 3)
        self.assertEqual(current["module_counts"]["with_enhanced"], 2)
        self.assertEqual(current["module_counts"]["with_verification"], 2)

    def test_modules_index_covers_plan_presence_and_does_not_parse_markdown(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        build_ui_data(generated, enhanced, output)

        index = json.loads((output / "modules-index.json").read_text(encoding="utf-8"))
        modules = {module["name"]: module for module in index["modules"]}
        self.assertEqual(sorted(modules), ["alpha", "beta", "missing"])
        self.assertTrue(modules["alpha"]["factual"]["present"])
        self.assertTrue(modules["alpha"]["enhanced"]["present"])
        self.assertTrue(modules["alpha"]["verification"]["present"])
        self.assertFalse(modules["missing"]["enhanced"]["present"])
        self.assertFalse(modules["missing"]["verification"]["present"])
        self.assertEqual(modules["alpha"]["verification"]["verification_status"], "verified_warning")
        self.assertEqual(modules["alpha"]["verification"]["verdict"], "warning")
        self.assertEqual(modules["alpha"]["verification"]["weak_claims_count"], 1)

    def test_history_index_uses_history_indexes_and_paths_are_forward_slash(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        build_ui_data(generated, enhanced, output)

        history = json.loads((output / "history-index.json").read_text(encoding="utf-8"))
        self.assertEqual(history["generation_runs"][0]["run_id"], "generation-run")
        self.assertEqual(history["verification_runs"][0]["run_id"], "verification-run")
        self.assertEqual(history["verification_runs"][0]["verification_mode"], "same_context")
        self.assert_no_backslashes(history)

    def test_outputs_are_deterministic_and_sources_are_not_mutated(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        source_paths = [
            enhanced / "llm-batch-run-manifest.json",
            enhanced / "verification" / "llm-batch-verification-manifest.json",
            enhanced / "history" / "generation" / "index.json",
            enhanced / "history" / "verification" / "index.json",
        ]
        before_sources = {path: path.read_text(encoding="utf-8") for path in source_paths}

        build_ui_data(generated, enhanced, output)
        first_outputs = {path.name: path.read_text(encoding="utf-8") for path in sorted(output.glob("*.json"))}
        build_ui_data(generated, enhanced, output)
        second_outputs = {path.name: path.read_text(encoding="utf-8") for path in sorted(output.glob("*.json"))}

        self.assertEqual(first_outputs, second_outputs)
        self.assertEqual(before_sources, {path: path.read_text(encoding="utf-8") for path in source_paths})

    def test_missing_optional_verification_files_and_dry_run_do_not_fail(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        (enhanced / "verification" / "module-package-beta.verification.json").unlink()

        result = build_ui_data(generated, enhanced, output, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertFalse(output.exists())
        build_ui_data(generated, enhanced, output)
        modules = {
            module["name"]: module
            for module in json.loads((output / "modules-index.json").read_text(encoding="utf-8"))["modules"]
        }
        self.assertEqual(modules["beta"]["verification"]["verification_status"], "missing")
        self.assertFalse(modules["missing"]["verification"]["present"])

    def test_strict_fails_when_required_source_manifest_is_missing(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        (generated / "doc-manifest.json").unlink()

        exit_code, stdout, stderr = self.capture_main(
            [
                "build-ui-data",
                "--generated",
                str(generated),
                "--enhanced",
                str(enhanced),
                "--output",
                str(output),
                "--strict",
            ],
            cwd=root,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Missing or invalid source manifest", stderr)

    def assert_no_backslashes(self, value: object) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                self.assert_no_backslashes(nested)
        elif isinstance(value, list):
            for nested in value:
                self.assert_no_backslashes(nested)
        elif isinstance(value, str):
            self.assertNotIn("\\", value)


def make_module(name: str, *, priority: str, explain_mode: str) -> dict:
    return {
        "name": name,
        "type": "package",
        "module_page_role": "detailed",
        "module_doc_path": f"modules/module-package-{name}.md",
        "module_doc_exists": True,
        "source_files": [f"src/{name}.py"],
        "file_doc_paths": [
            {
                "source_file": f"src/{name}.py",
                "doc_path": f"files/file-src-{name}-py.md",
                "exists": True,
            }
        ],
        "priority": priority,
        "explain_mode": explain_mode,
    }


if __name__ == "__main__":
    unittest.main()
