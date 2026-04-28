from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.request import urlopen
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.cli import main  # noqa: E402
from docgen.ui_server import build_server_config, create_ui_server  # noqa: E402


class UiServerCliTests(unittest.TestCase):
    def make_temp_dir(self) -> Path:
        temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temp_directory.cleanup)
        return Path(temp_directory.name)

    def capture_main(self, argv: list[str], *, cwd: Path) -> tuple[int, str, str]:
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
        ui_data = root / "docs" / "ui-data"
        generated_module = generated / "modules" / "module-package-llm.md"
        generated_file = generated / "files" / "file-src-docgen-llm-config-py.md"
        enhanced_module = enhanced / "modules" / "module-package-llm.md"
        verification_summary = enhanced / "verification" / "module-package-llm.verification.md"
        verification_json = enhanced / "verification" / "module-package-llm.verification.json"

        generated_module.parent.mkdir(parents=True, exist_ok=True)
        generated_file.parent.mkdir(parents=True, exist_ok=True)
        enhanced_module.parent.mkdir(parents=True, exist_ok=True)
        verification_summary.parent.mkdir(parents=True, exist_ok=True)
        generated_module.write_text("# Factual llm\n\nFactual layer text.\n", encoding="utf-8")
        generated_file.write_text("# File config\n\nFile documentation.\n", encoding="utf-8")
        enhanced_module.write_text("# Enhanced llm\n\nEnhanced explanation.\n", encoding="utf-8")
        verification_summary.write_text("# Verification llm\n\nVerification summary.\n", encoding="utf-8")
        self.write_json(
            verification_json,
            {
                "module": "llm",
                "verifier_status": "ok",
                "structured_output_valid": True,
                "verdict": "warning",
                "weak_claims": [{}],
            },
        )

        module_record = {
            "name": "llm",
            "type": "package",
            "module_page_role": "detailed",
            "explain_mode": "full",
            "priority": "high",
            "factual": {
                "present": True,
                "module_doc_path": "docs/generated/modules/module-package-llm.md",
                "source_files": ["src/docgen/llm/config.py"],
                "file_doc_paths": [
                    {
                        "source_file": "src/docgen/llm/config.py",
                        "doc_path": "docs/generated/files/file-src-docgen-llm-config-py.md",
                        "exists": True,
                    }
                ],
            },
            "enhanced": {
                "present": True,
                "markdown_path": "docs/enhanced/modules/module-package-llm.md",
                "metadata_path": "docs/enhanced/llm-runs/module-package-llm.metadata.json",
                "generation_status": "generated",
                "generation_run_id": "generation-run",
                "context_fingerprint": "fingerprint",
            },
            "verification": {
                "present": True,
                "json_path": "docs/enhanced/verification/module-package-llm.verification.json",
                "summary_path": "docs/enhanced/verification/module-package-llm.verification.md",
                "verification_status": "verified_warning",
                "verifier_status": "ok",
                "structured_output_valid": True,
                "verdict": "warning",
                "verification_run_id": "verification-run",
                "unsupported_claims_count": 0,
                "weak_claims_count": 1,
                "missing_uncertainty_count": 0,
                "missing_factual_support_count": 0,
            },
            "links": {
                "generation_history_manifest_path": "docs/enhanced/history/generation/generation-run.json",
                "verification_history_manifest_path": "docs/enhanced/history/verification/verification-run.json",
            },
        }
        self.write_json(
            ui_data / "current-state.json",
            {
                "schema_version": "1.0",
                "latest_generation_run": {"run_id": "generation-run"},
                "latest_verification_run": {"run_id": "verification-run"},
                "module_counts": {
                    "total_modules": 1,
                    "verification_pass": 0,
                    "verification_warning": 1,
                    "verification_fail": 0,
                    "missing_enhanced": 0,
                    "missing_verification": 0,
                },
            },
        )
        self.write_json(
            ui_data / "modules-index.json",
            {"schema_version": "1.0", "modules": [module_record], "warnings": []},
        )
        self.write_json(
            ui_data / "history-index.json",
            {
                "schema_version": "1.0",
                "generation_runs": [
                    {
                        "run_id": "generation-run",
                        "generated_at": "2026-04-28T00:00:00+00:00",
                        "manifest_path": "docs/enhanced/history/generation/generation-run.json",
                        "dry_run": False,
                        "provider": "openrouter",
                        "model": "model",
                        "selected_modules": ["llm"],
                        "generated_count": 1,
                        "skipped_cached_count": 0,
                        "skipped_by_plan_count": 0,
                        "failed_count": 0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                ],
                "verification_runs": [
                    {
                        "run_id": "verification-run",
                        "generated_at": "2026-04-28T00:01:00+00:00",
                        "manifest_path": "docs/enhanced/history/verification/verification-run.json",
                        "dry_run": False,
                        "provider": "openrouter",
                        "model": "model",
                        "verification_mode": "same_context",
                        "selected_modules": ["llm"],
                        "verified_count": 1,
                        "warning_count": 1,
                        "failed_count": 0,
                        "skipped_cached_count": 0,
                        "skipped_missing_enhanced_count": 0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                ],
                "warnings": [],
            },
        )
        self.write_json(
            ui_data / "ui-data-manifest.json",
            {
                "schema_version": "1.0",
                "files": {
                    "current_state": "docs/ui-data/current-state.json",
                    "modules_index": "docs/ui-data/modules-index.json",
                    "history_index": "docs/ui-data/history-index.json",
                },
                "warnings": [],
            },
        )
        return generated, enhanced, ui_data

    def start_server(self, generated: Path, enhanced: Path, ui_data: Path) -> tuple[str, object, threading.Thread]:
        config = build_server_config(generated, enhanced, ui_data, strict=True)
        server = create_ui_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            server.shutdown()
            server.server_close()
            thread.join(2)

        self.addCleanup(cleanup)
        return f"http://127.0.0.1:{server.server_port}", server, thread

    def test_serve_ui_dry_run_does_not_require_node_or_start_server(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)

        with mock.patch("subprocess.Popen", side_effect=AssertionError("Node/npm must not be required")):
            exit_code, stdout, stderr = self.capture_main(
                [
                    "serve-ui",
                    "--generated",
                    str(generated),
                    "--enhanced",
                    str(enhanced),
                    "--ui-data",
                    str(ui_data),
                    "--dry-run",
                ],
                cwd=root,
            )

        self.assertEqual(exit_code, 0, msg=stderr)
        summary = json.loads(stdout)
        self.assertTrue(summary["dry_run"])
        self.assertFalse(summary["network_call"])
        self.assertIn("/module/{name}", summary["routes"])

    def test_server_routes_ui_data_and_pages_are_served(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)

        home = urlopen(base_url + "/").read().decode("utf-8")
        modules = urlopen(base_url + "/modules").read().decode("utf-8")
        module = urlopen(base_url + "/module/llm").read().decode("utf-8")
        current_state = json.load(urlopen(base_url + "/ui-data/current-state.json"))

        self.assertIn("Project Inspector", home)
        self.assertIn("generation-run", home)
        self.assertIn("/history", home)
        self.assertIn("/module/llm", modules)
        self.assertIn("Factual", module)
        self.assertIn("Enhanced", module)
        self.assertIn("Verification", module)
        self.assertIn("Related Files", module)
        self.assertIn("/file?path=", module)
        self.assertIn("/history/generation/generation-run", module)
        self.assertIn("Factual layer text", module)
        self.assertEqual(current_state["latest_generation_run"]["run_id"], "generation-run")
        self.assertNotIn("https://", home + modules + module)
        self.assertNotIn("cdn", (home + modules + module).lower())

    def test_history_and_file_routes_are_served(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)

        history = urlopen(base_url + "/history").read().decode("utf-8")
        run = urlopen(base_url + "/history/verification/verification-run").read().decode("utf-8")
        file_page = urlopen(
            base_url + "/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-llm-config-py.md"
        ).read().decode("utf-8")

        self.assertIn("Generation Runs", history)
        self.assertIn("Verification Runs", history)
        self.assertIn("Selected Modules", run)
        self.assertIn("/module/llm", run)
        self.assertIn("File config", file_page)
        self.assertIn("Related Modules", file_page)

    def test_server_is_read_only_for_artifacts(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        tracked_files = [
            ui_data / "current-state.json",
            ui_data / "modules-index.json",
            ui_data / "history-index.json",
            generated / "modules" / "module-package-llm.md",
            enhanced / "modules" / "module-package-llm.md",
        ]
        before = {path: path.read_text(encoding="utf-8") for path in tracked_files}
        base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)

        urlopen(base_url + "/").read()
        urlopen(base_url + "/modules").read()
        urlopen(base_url + "/module/llm").read()
        urlopen(base_url + "/ui-data/current-state.json").read()

        after = {path: path.read_text(encoding="utf-8") for path in tracked_files}
        self.assertEqual(before, after)

    def test_strict_mode_reports_incomplete_ui_data(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        (ui_data / "modules-index.json").unlink()

        exit_code, stdout, stderr = self.capture_main(
            [
                "serve-ui",
                "--generated",
                str(generated),
                "--enhanced",
                str(enhanced),
                "--ui-data",
                str(ui_data),
                "--strict",
                "--dry-run",
            ],
            cwd=root,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Missing or invalid UI data file", stderr)


if __name__ == "__main__":
    unittest.main()
