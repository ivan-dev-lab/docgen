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
from urllib.error import HTTPError
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
                "weak_claims": [
                    {
                        "section": "Overview",
                        "claim_text": "The LLM claim is weak.",
                        "reason": "It needs factual support.",
                        "suggested_rewrite": "Make the claim narrower.",
                    }
                ],
                "unsupported_claims": [],
                "missing_uncertainty": [],
                "missing_factual_support": [],
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
                    },
                    {
                        "run_id": "generation-run-prev",
                        "generated_at": "2026-04-27T00:00:00+00:00",
                        "manifest_path": "docs/enhanced/history/generation/generation-run-prev.json",
                        "dry_run": False,
                        "provider": "openrouter",
                        "model": "model",
                        "selected_modules": ["llm"],
                        "generated_count": 1,
                        "skipped_cached_count": 0,
                        "skipped_by_plan_count": 0,
                        "failed_count": 0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
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
                    },
                    {
                        "run_id": "verification-run-prev",
                        "generated_at": "2026-04-27T00:01:00+00:00",
                        "manifest_path": "docs/enhanced/history/verification/verification-run-prev.json",
                        "dry_run": False,
                        "provider": "openrouter",
                        "model": "model",
                        "verification_mode": "same_context",
                        "selected_modules": ["llm"],
                        "verified_count": 1,
                        "warning_count": 0,
                        "failed_count": 1,
                        "skipped_cached_count": 0,
                        "skipped_missing_enhanced_count": 0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                    }
                ],
                "warnings": [],
            },
        )
        self.write_json(
            ui_data / "history-runs.json",
            {
                "schema_version": "1.0",
                "generation_runs": [
                    {
                        "run_id": "generation-run",
                        "kind": "generation",
                        "generated_at": "2026-04-28T00:00:00+00:00",
                        "manifest_path": "docs/enhanced/history/generation/generation-run.json",
                        "dry_run": False,
                        "latest_live_run": True,
                        "provider": "openrouter",
                        "model": "model",
                        "selected_modules": ["llm"],
                        "selected_modules_count": 1,
                        "generated_count": 1,
                        "skipped_cached_count": 0,
                        "skipped_by_plan_count": 0,
                        "failed_count": 0,
                        "cache_hit_rate": 0.0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                        "result_status_counts": {"generated": 1},
                        "results": [
                            {
                                "module": "llm",
                                "status": "generated",
                                "priority": "high",
                                "explain_mode": "full",
                                "cache_hit": False,
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                                "duration_seconds": 1.5,
                                "output_path": "docs/enhanced/modules/module-package-llm.md",
                                "metadata_path": "docs/enhanced/llm-runs/module-package-llm.metadata.json",
                                "error": None,
                            }
                        ],
                    },
                    {
                        "run_id": "generation-run-prev",
                        "kind": "generation",
                        "generated_at": "2026-04-27T00:00:00+00:00",
                        "manifest_path": "docs/enhanced/history/generation/generation-run-prev.json",
                        "dry_run": False,
                        "latest_live_run": False,
                        "provider": "openrouter",
                        "model": "model",
                        "selected_modules": ["llm"],
                        "selected_modules_count": 1,
                        "generated_count": 1,
                        "skipped_cached_count": 0,
                        "skipped_by_plan_count": 0,
                        "failed_count": 0,
                        "cache_hit_rate": 0.0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                        "result_status_counts": {"generated": 1},
                        "results": [
                            {
                                "module": "llm",
                                "status": "generated",
                                "priority": "high",
                                "explain_mode": "full",
                                "cache_hit": False,
                                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                                "duration_seconds": 1.0,
                                "output_path": "docs/enhanced/modules/module-package-llm.md",
                                "metadata_path": "docs/enhanced/llm-runs/module-package-llm.metadata.json",
                                "context_fingerprint": "old-fingerprint",
                                "error": None,
                            }
                        ],
                    }
                ],
                "verification_runs": [
                    {
                        "run_id": "verification-run",
                        "kind": "verification",
                        "generated_at": "2026-04-28T00:01:00+00:00",
                        "manifest_path": "docs/enhanced/history/verification/verification-run.json",
                        "dry_run": False,
                        "latest_live_run": True,
                        "provider": "openrouter",
                        "model": "model",
                        "verification_mode": "same_context",
                        "selected_modules": ["llm"],
                        "selected_modules_count": 1,
                        "verified_count": 1,
                        "warning_count": 1,
                        "failed_count": 0,
                        "skipped_cached_count": 0,
                        "skipped_missing_enhanced_count": 0,
                        "cache_hit_rate": 0.0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                        "result_status_counts": {"verified_warning": 1},
                        "results": [
                            {
                                "module": "llm",
                                "status": "verified_warning",
                                "verifier_status": "ok",
                                "structured_output_valid": True,
                                "verdict": "warning",
                                "cache_hit": False,
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                                "weak_claims_count": 1,
                                "unsupported_claims_count": 0,
                                "missing_factual_support_count": 0,
                                "missing_uncertainty_count": 0,
                                "verification_json_path": "docs/enhanced/verification/module-package-llm.verification.json",
                                "verification_summary_path": "docs/enhanced/verification/module-package-llm.verification.md",
                                "enhanced_markdown_path": "docs/enhanced/modules/module-package-llm.md",
                                "error": None,
                            }
                        ],
                    },
                    {
                        "run_id": "verification-run-prev",
                        "kind": "verification",
                        "generated_at": "2026-04-27T00:01:00+00:00",
                        "manifest_path": "docs/enhanced/history/verification/verification-run-prev.json",
                        "dry_run": False,
                        "latest_live_run": False,
                        "provider": "openrouter",
                        "model": "model",
                        "verification_mode": "same_context",
                        "selected_modules": ["llm"],
                        "selected_modules_count": 1,
                        "verified_count": 1,
                        "warning_count": 0,
                        "failed_count": 1,
                        "skipped_cached_count": 0,
                        "skipped_missing_enhanced_count": 0,
                        "cache_hit_rate": 0.0,
                        "usage_totals": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                        "result_status_counts": {"verified_fail": 1},
                        "results": [
                            {
                                "module": "llm",
                                "status": "verified_fail",
                                "verifier_status": "ok",
                                "structured_output_valid": True,
                                "verdict": "fail",
                                "cache_hit": False,
                                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
                                "weak_claims_count": 2,
                                "unsupported_claims_count": 0,
                                "missing_factual_support_count": 1,
                                "missing_uncertainty_count": 0,
                                "verification_json_path": "docs/enhanced/verification/module-package-llm.verification.json",
                                "verification_summary_path": "docs/enhanced/verification/module-package-llm.verification.md",
                                "enhanced_markdown_path": "docs/enhanced/modules/module-package-llm.md",
                                "error": None,
                            }
                        ],
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
                    "history_runs": "docs/ui-data/history-runs.json",
                    "problems_index": "docs/ui-data/problems-index.json",
                    "files_index": "docs/ui-data/files-index.json",
                    "functions_index": "docs/ui-data/functions-index.json",
                    "search_index": "docs/ui-data/search-index.json",
                },
                "warnings": [],
            },
        )
        self.write_json(
            ui_data / "problems-index.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:01:00+00:00",
                "status": "ok",
                "summary": {
                    "modules_with_warnings": 1,
                    "modules_with_failures": 0,
                    "modules_missing_enhanced": 0,
                    "modules_missing_verification": 0,
                    "weak_claims_total": 1,
                    "unsupported_claims_total": 0,
                    "missing_factual_support_total": 0,
                    "missing_uncertainty_total": 0,
                },
                "module_problems": [
                    {
                        "module": "llm",
                        "severity": "warning",
                        "problem_types": ["verification_warning"],
                        "verification_verdict": "warning",
                        "weak_claims_count": 1,
                        "unsupported_claims_count": 0,
                        "missing_factual_support_count": 0,
                        "missing_uncertainty_count": 0,
                        "enhanced_present": True,
                        "verification_present": True,
                        "module_path": "/module/llm",
                        "verification_json_path": "docs/enhanced/verification/module-package-llm.verification.json",
                        "verification_summary_path": "docs/enhanced/verification/module-package-llm.verification.md",
                    }
                ],
                "issue_problems": [
                    {
                        "module": "llm",
                        "issue_type": "weak_claim",
                        "severity": "warning",
                        "section": "Overview",
                        "reason": "It needs factual support.",
                        "claim_text": "The LLM claim is weak.",
                        "suggested_rewrite": "Make the claim narrower.",
                        "module_path": "/module/llm",
                        "verification_json_path": "docs/enhanced/verification/module-package-llm.verification.json",
                        "verification_summary_path": "docs/enhanced/verification/module-package-llm.verification.md",
                    }
                ],
                "warnings": [],
            },
        )
        self.write_json(
            ui_data / "files-index.json",
            {
                "schema_version": "1.0",
                "files": [
                    {
                        "path": "src/docgen/ui_server.py",
                        "doc_path": "docs/generated/files/file-src-docgen-llm-config-py.md",
                        "module_names": ["llm"],
                        "entity_count": 1,
                        "import_count": 2,
                        "present_in_generated_docs": True,
                    }
                ],
                "warnings": [],
            },
        )
        self.write_json(
            ui_data / "functions-index.json",
            {
                "schema_version": "1.0",
                "functions": [
                    {
                        "name": "serve_ui",
                        "qualified_name": "docgen.ui_server.serve_ui",
                        "source_file": "src/docgen/ui_server.py",
                        "doc_path": "docs/generated/files/file-src-docgen-llm-config-py.md",
                        "module_names": ["llm"],
                        "present_in_function_index": True,
                    }
                ],
                "warnings": [],
            },
        )
        self.write_json(
            ui_data / "search-index.json",
            {
                "schema_version": "1.0",
                "records": [
                    {
                        "entity_kind": "module",
                        "entity_id": "llm",
                        "title": "llm",
                        "subtitle": "package / detailed",
                        "module_name": "llm",
                        "path": "docs/generated/modules/module-package-llm.md",
                        "search_text": "llm package detailed Enhanced explanation Factual layer",
                        "type": "package",
                        "role": "detailed",
                        "verification_verdict": "warning",
                        "run_kind": None,
                        "run_id": None,
                        "problem_type": None,
                        "severity": None,
                        "links": {
                            "ui_path": "/module/llm",
                            "artifact_path": "docs/enhanced/modules/module-package-llm.md",
                        },
                    },
                    {
                        "entity_kind": "file",
                        "entity_id": "src/docgen/ui_server.py",
                        "title": "ui_server.py",
                        "subtitle": "src/docgen/ui_server.py",
                        "module_name": "llm",
                        "path": "src/docgen/ui_server.py",
                        "search_text": "src/docgen/ui_server.py ui_server.py local ui server",
                        "type": "source",
                        "role": None,
                        "verification_verdict": None,
                        "run_kind": None,
                        "run_id": None,
                        "problem_type": None,
                        "severity": None,
                        "links": {
                            "ui_path": "/file?path=docs/generated/files/file-src-docgen-llm-config-py.md",
                            "artifact_path": "docs/generated/files/file-src-docgen-llm-config-py.md",
                        },
                    },
                    {
                        "entity_kind": "function",
                        "entity_id": "docgen.ui_server.serve_ui",
                        "title": "serve_ui",
                        "subtitle": "src/docgen/ui_server.py",
                        "module_name": "llm",
                        "path": "src/docgen/ui_server.py",
                        "search_text": "serve_ui docgen.ui_server.serve_ui",
                        "type": "function",
                        "role": None,
                        "verification_verdict": None,
                        "run_kind": None,
                        "run_id": None,
                        "problem_type": None,
                        "severity": None,
                        "links": {
                            "ui_path": "/file?path=docs/generated/files/file-src-docgen-llm-config-py.md",
                            "artifact_path": "docs/generated/files/file-src-docgen-llm-config-py.md",
                        },
                    },
                    {
                        "entity_kind": "verification_run",
                        "entity_id": "verification-run",
                        "title": "verification-run",
                        "subtitle": "verification run",
                        "module_name": None,
                        "path": "docs/enhanced/history/verification/verification-run.json",
                        "search_text": "verification-run verification llm",
                        "type": None,
                        "role": None,
                        "verification_verdict": None,
                        "run_kind": "verification",
                        "run_id": "verification-run",
                        "problem_type": None,
                        "severity": None,
                        "links": {
                            "ui_path": "/history/verification/verification-run",
                            "artifact_path": "docs/enhanced/history/verification/verification-run.json",
                        },
                    },
                    {
                        "entity_kind": "problem",
                        "entity_id": "llm:weak_claim",
                        "title": "llm weak_claim",
                        "subtitle": "It needs factual support.",
                        "module_name": "llm",
                        "path": "docs/enhanced/verification/module-package-llm.verification.json",
                        "search_text": "llm weak_claim warning The LLM claim is weak.",
                        "type": None,
                        "role": None,
                        "verification_verdict": None,
                        "run_kind": None,
                        "run_id": None,
                        "problem_type": "weak_claim",
                        "severity": "warning",
                        "links": {
                            "ui_path": "/problems?module=llm&type=weak_claim",
                            "artifact_path": "docs/enhanced/verification/module-package-llm.verification.json",
                        },
                    },
                ],
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
        problems = urlopen(base_url + "/problems").read().decode("utf-8")
        problems_by_module = urlopen(base_url + "/problems?module=llm").read().decode("utf-8")
        problems_by_type = urlopen(base_url + "/problems?type=weak_claim").read().decode("utf-8")
        problems_by_severity = urlopen(base_url + "/problems?severity=warning").read().decode("utf-8")
        search = urlopen(base_url + "/search?q=llm").read().decode("utf-8")
        file_search = urlopen(base_url + "/search?q=ui_server.py&kind=file").read().decode("utf-8")
        function_search = urlopen(base_url + "/search?q=serve_ui&kind=function").read().decode("utf-8")
        verdict_search = urlopen(base_url + "/search?kind=module&verdict=warning").read().decode("utf-8")
        role_search = urlopen(base_url + "/search?kind=module&role=detailed").read().decode("utf-8")
        severity_search = urlopen(base_url + "/search?kind=problem&severity=warning").read().decode("utf-8")
        run_search = urlopen(base_url + "/search?run_kind=verification").read().decode("utf-8")
        no_results = urlopen(base_url + "/search?q=not-present").read().decode("utf-8")
        current_state = json.load(urlopen(base_url + "/ui-data/current-state.json"))

        self.assertIn("Project Inspector", home)
        self.assertIn('action="/search"', home)
        self.assertIn("generation-run", home)
        self.assertIn("/problems", home)
        self.assertIn("/history", home)
        self.assertIn("#warning-modules", home)
        self.assertIn("#missing-enhanced", home)
        self.assertIn("#missing-verification", home)
        self.assertIn("Latest generation run", home)
        self.assertIn("Verification warning", home)
        self.assertIn("/module/llm", modules)
        self.assertIn("Module Summary", module)
        self.assertIn("Problems", module)
        self.assertIn("/problems?module=llm", module)
        self.assertIn("Обзор", module)
        self.assertIn("Факты", module)
        self.assertIn("ИИ-объяснение", module)
        self.assertIn("Проверка", module)
        self.assertIn("Связанные файлы", module)
        self.assertIn("История", module)
        self.assertIn("Factual", module)
        self.assertIn("Enhanced explanation", module)
        self.assertIn("Verification", module)
        self.assertIn("Structured verification summary", module)
        self.assertLess(module.index("Structured verification summary"), module.index("Verification summary."))
        self.assertIn("Weak claims", module)
        self.assertIn("Unsupported claims", module)
        self.assertIn("Related Files", module)
        self.assertIn("/file?path=", module)
        self.assertIn("/history/generation/generation-run", module)
        self.assertIn("Factual layer text", module)
        self.assertIn("Enhanced explanation.", module)
        self.assertIn("Problems / Проблемы", problems)
        self.assertIn("Module-level problems", problems)
        self.assertIn("Issue-level problems", problems)
        self.assertIn("weak_claim", problems)
        self.assertIn("/module/llm", problems)
        self.assertIn("/artifact?path=", problems)
        self.assertIn("Active filters: module=llm", problems_by_module)
        self.assertIn("The LLM claim is weak.", problems_by_type)
        self.assertIn("Active filters: severity=warning", problems_by_severity)
        self.assertIn("Search / Поиск", search)
        self.assertIn("Results count", search)
        self.assertIn("/module/llm", search)
        self.assertIn("ui_server.py", file_search)
        self.assertIn("serve_ui", function_search)
        self.assertIn("verdict: warning", verdict_search)
        self.assertIn("role: detailed", role_search)
        self.assertIn("weak_claim", severity_search)
        self.assertIn("verification-run", run_search)
        self.assertIn("No search results", no_results)
        self.assertEqual(current_state["latest_generation_run"]["run_id"], "generation-run")
        self.assertNotIn("https://", home + modules + module + problems + search)
        self.assertNotIn("cdn", (home + modules + module + problems + search).lower())

    def test_history_and_file_routes_are_served(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)

        history = urlopen(base_url + "/history").read().decode("utf-8")
        generation_run = urlopen(base_url + "/history/generation/generation-run").read().decode("utf-8")
        run = urlopen(base_url + "/history/verification/verification-run").read().decode("utf-8")
        compare = urlopen(base_url + "/compare").read().decode("utf-8")
        generation_compare = urlopen(
            base_url + "/compare/generation?run_a=generation-run-prev&run_b=generation-run"
        ).read().decode("utf-8")
        verification_compare = urlopen(
            base_url + "/compare/verification?run_a=verification-run-prev&run_b=verification-run"
        ).read().decode("utf-8")
        file_page = urlopen(
            base_url + "/file?path=docs%2Fgenerated%2Ffiles%2Ffile-src-docgen-llm-config-py.md"
        ).read().decode("utf-8")
        artifact_page = urlopen(
            base_url + "/artifact?path=docs%2Fenhanced%2Fmodules%2Fmodule-package-llm.md"
        ).read().decode("utf-8")

        self.assertIn("Generation Runs", history)
        self.assertIn("Verification Runs", history)
        self.assertIn("latest live", history)
        self.assertIn("Run Summary", generation_run)
        self.assertIn("Results", generation_run)
        self.assertIn("generated 1, cached 0, failed 0", history)
        self.assertIn("Run Summary", run)
        self.assertIn("Results", run)
        self.assertIn("verified_warning", run)
        self.assertIn("warning", run)
        self.assertIn("/module/llm", run)
        self.assertIn("/artifact?path=", run)
        self.assertIn("Compare / Сравнение", compare)
        self.assertIn("Compare latest two generation runs", compare)
        self.assertIn("Generation Compare", generation_compare)
        self.assertIn("changed", generation_compare)
        self.assertIn("usage.total_tokens", generation_compare)
        self.assertIn("/module/llm", generation_compare)
        self.assertIn("/history/generation/generation-run", generation_compare)
        self.assertIn("Verification Compare", verification_compare)
        self.assertIn("verdict", verification_compare.lower())
        self.assertIn("improved", verification_compare)
        self.assertIn("weak -1", verification_compare)
        self.assertIn("/module/llm", verification_compare)
        self.assertIn("/history/verification/verification-run", verification_compare)
        self.assertIn("File config", file_page)
        self.assertIn("Related Modules", file_page)
        self.assertIn("Artifact", artifact_page)
        self.assertIn("Enhanced explanation.", artifact_page)

    def test_invalid_history_run_returns_not_found_and_empty_history_renders(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)

        with self.assertRaises(HTTPError) as raised:
            urlopen(base_url + "/history/generation/missing-run")
        self.assertEqual(raised.exception.code, 404)

        with self.assertRaises(HTTPError) as compare_raised:
            urlopen(base_url + "/compare/generation?run_a=missing-run&run_b=generation-run")
        self.assertEqual(compare_raised.exception.code, 404)

        self.write_json(
            ui_data / "history-runs.json",
            {"schema_version": "1.0", "generation_runs": [], "verification_runs": [], "warnings": []},
        )
        config = build_server_config(generated, enhanced, ui_data, strict=True)
        empty_server = create_ui_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=empty_server.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            empty_server.shutdown()
            empty_server.server_close()
            thread.join(2)

        self.addCleanup(cleanup)
        empty_history = urlopen(f"http://127.0.0.1:{empty_server.server_port}/history").read().decode("utf-8")
        self.assertIn("no history runs", empty_history)

    def test_problems_no_data_and_no_problems_are_distinct(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        self.write_json(
            ui_data / "problems-index.json",
            {
                "schema_version": "1.0",
                "status": "no_data",
                "summary": {},
                "module_problems": [],
                "issue_problems": [],
                "warnings": [],
            },
        )
        no_data_base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)
        no_data_page = urlopen(no_data_base_url + "/problems").read().decode("utf-8")
        self.assertIn("Недостаточно данных", no_data_page)

        root_ok = self.make_temp_dir()
        generated_ok, enhanced_ok, ui_data_ok = self.build_fixture(root_ok)
        self.write_json(
            ui_data_ok / "problems-index.json",
            {
                "schema_version": "1.0",
                "status": "ok",
                "summary": {},
                "module_problems": [],
                "issue_problems": [],
                "warnings": [],
            },
        )
        ok_base_url, _ok_server, _ok_thread = self.start_server(generated_ok, enhanced_ok, ui_data_ok)
        ok_page = urlopen(ok_base_url + "/problems").read().decode("utf-8")
        self.assertIn("Проблем не найдено", ok_page)
        self.assertNotEqual(no_data_page, ok_page)

    def test_server_is_read_only_for_artifacts(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, ui_data = self.build_fixture(root)
        tracked_files = [
            ui_data / "current-state.json",
            ui_data / "modules-index.json",
            ui_data / "history-index.json",
            ui_data / "history-runs.json",
            ui_data / "problems-index.json",
            ui_data / "files-index.json",
            ui_data / "functions-index.json",
            ui_data / "search-index.json",
            generated / "modules" / "module-package-llm.md",
            enhanced / "modules" / "module-package-llm.md",
        ]
        before = {path: path.read_text(encoding="utf-8") for path in tracked_files}
        base_url, _server, _thread = self.start_server(generated, enhanced, ui_data)

        urlopen(base_url + "/").read()
        urlopen(base_url + "/modules").read()
        urlopen(base_url + "/module/llm").read()
        urlopen(base_url + "/problems").read()
        urlopen(base_url + "/search?q=llm").read()
        urlopen(base_url + "/compare").read()
        urlopen(base_url + "/compare/generation?run_a=generation-run-prev&run_b=generation-run").read()
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
