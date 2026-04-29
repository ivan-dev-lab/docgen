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
from docgen.ui_content_contract import FORBIDDEN_MARKDOWN_SEMANTIC_FIELDS  # noqa: E402
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
        files_dir = generated / "files"
        modules_dir.mkdir(parents=True, exist_ok=True)
        files_dir.mkdir(parents=True, exist_ok=True)
        for name in ("alpha", "beta", "missing"):
            (modules_dir / f"module-package-{name}.md").write_text(
                f"# {name}\n\nThis markdown says warning fail pass and weak claims: 999.\n",
                encoding="utf-8",
            )
            (files_dir / f"file-src-{name}-py.md").write_text(
                f"# src/{name}.py\n\nFactual file documentation for {name}.\n",
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
                "file_pages": [
                    {
                        "source_file": f"src/{name}.py",
                        "doc_path": f"files/file-src-{name}-py.md",
                        "entity_count": 1,
                        "import_count": 2,
                    }
                    for name in ("alpha", "beta", "missing")
                ],
            },
        )

        for name in ("alpha", "beta"):
            enhanced_path = enhanced / "modules" / f"module-package-{name}.md"
            enhanced_path.parent.mkdir(parents=True, exist_ok=True)
            enhanced_path.write_text(
                "# Enhanced\n\nwarning fail pass\nverdict: fail\nweak claims: 999\nunsupported_claims: 999\n",
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
        self.write_json(
            enhanced / "history" / "generation" / "generation-run.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:01:00+00:00",
                "run_id": "generation-run",
                "provider": "openrouter",
                "model": "model-a",
                "dry_run": False,
                "selected_modules": ["alpha", "beta"],
                "total_modules_selected": 2,
                "generated_count": 1,
                "skipped_cached_count": 1,
                "skipped_by_plan_count": 0,
                "failed_count": 0,
                "usage_totals": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "results": [
                    {
                        "module": "alpha",
                        "status": "generated",
                        "priority": "high",
                        "explain_mode": "full",
                        "cache_hit": False,
                        "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
                        "duration_seconds": 1.2,
                        "output_path": "docs/enhanced/modules/module-package-alpha.md",
                        "metadata_path": "docs/enhanced/llm-runs/module-package-alpha.metadata.json",
                    },
                    {
                        "module": "beta",
                        "status": "skipped_cached",
                        "priority": "medium",
                        "explain_mode": "summary",
                        "cache_hit": True,
                        "output_path": "docs/enhanced/modules/module-package-beta.md",
                        "metadata_path": "docs/enhanced/llm-runs/module-package-beta.metadata.json",
                    },
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
                    "weak_claims": [
                        {
                            "section": "Usage",
                            "claim_text": "Alpha claim is too broad.",
                            "reason": "Needs a stronger source.",
                            "suggested_rewrite": "Narrow the alpha claim.",
                        }
                    ]
                    if verdict == "warning"
                    else [],
                    "missing_uncertainty": [],
                    "missing_factual_support": [
                        {
                            "section": "Usage",
                            "claim_text": "Alpha support is absent.",
                            "reason": "No factual support cited.",
                        }
                    ]
                    if verdict == "warning"
                    else [],
                },
            )
            (verification_root / f"module-package-{name}.verification.md").write_text(
                "# Human summary\n\nwarning fail pass\nverdict: fail\nweak_claims: 999\n",
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
            enhanced / "history" / "verification" / "verification-run.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:02:00+00:00",
                "run_id": "verification-run",
                "provider": "openrouter",
                "model": "model-a",
                "verification_mode": "same_context",
                "dry_run": False,
                "selected_modules": ["alpha", "beta"],
                "total_modules_selected": 2,
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
                        "verifier_status": "ok",
                        "structured_output_valid": True,
                        "verdict": "warning",
                        "cache_hit": False,
                        "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
                        "verification_json_path": "docs/enhanced/verification/module-package-alpha.verification.json",
                        "verification_summary_path": "docs/enhanced/verification/module-package-alpha.verification.md",
                        "enhanced_markdown_path": "docs/enhanced/modules/module-package-alpha.md",
                    },
                    {
                        "module": "beta",
                        "status": "verified_pass",
                        "verifier_status": "ok",
                        "structured_output_valid": True,
                        "verdict": "pass",
                        "cache_hit": False,
                        "verification_json_path": "docs/enhanced/verification/module-package-beta.verification.json",
                        "verification_summary_path": "docs/enhanced/verification/module-package-beta.verification.md",
                        "enhanced_markdown_path": "docs/enhanced/modules/module-package-beta.md",
                    },
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

    def build_analysis_fixture(self, root: Path) -> Path:
        analysis = root / ".docgen-analysis-live"
        self.write_json(
            analysis / "inventory.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:00:02+00:00",
                "files": [
                    {"path": f"src/{name}.py", "file_type": "source", "language": "python"}
                    for name in ("alpha", "beta", "missing")
                ],
            },
        )
        self.write_json(
            analysis / "function-index.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-04-28T00:00:03+00:00",
                "entities": [
                    {
                        "name": "serve_alpha",
                        "file": "src/alpha.py",
                        "line_start": 10,
                        "line_end": 20,
                        "entity_type": "function",
                        "container": "src.alpha",
                        "signature": "def serve_alpha() -> None",
                    },
                    {
                        "name": "Beta",
                        "file": "src/beta.py",
                        "line_start": 1,
                        "line_end": 8,
                        "entity_type": "class",
                        "container": "src.beta",
                    },
                ],
            },
        )
        return analysis

    def test_build_ui_data_creates_contract_files_and_current_state(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        analysis = self.build_analysis_fixture(root)

        exit_code, stdout, stderr = self.capture_main(
            [
                "build-ui-data",
                "--analysis",
                str(analysis),
                "--generated",
                str(generated),
                "--enhanced",
                str(enhanced),
                "--output",
                str(output),
            ],
            cwd=root,
        )

        self.assertEqual(exit_code, 0, msg=stderr)
        summary = json.loads(stdout)
        self.assertFalse(summary["network_call"])
        for name in (
            "current-state.json",
            "modules-index.json",
            "history-index.json",
            "history-runs.json",
            "problems-index.json",
            "files-index.json",
            "functions-index.json",
            "search-index.json",
            "ui-data-manifest.json",
        ):
            self.assertTrue((output / name).is_file(), msg=name)

        current = json.loads((output / "current-state.json").read_text(encoding="utf-8"))
        self.assertEqual(current["latest_generation_run"]["run_id"], "generation-run")
        self.assertEqual(current["latest_verification_run"]["run_id"], "verification-run")
        self.assertEqual(current["module_counts"]["total_modules"], 3)
        self.assertEqual(current["module_counts"]["with_enhanced"], 2)
        self.assertEqual(current["module_counts"]["with_verification"], 2)
        manifest = json.loads((output / "ui-data-manifest.json").read_text(encoding="utf-8"))
        self.assertIn("files_index", manifest["files"])
        self.assertIn("functions_index", manifest["files"])
        self.assertIn("search_index", manifest["files"])

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
        self.assertEqual(modules["alpha"]["verification"]["missing_factual_support_count"], 1)

    def test_poisoned_markdown_does_not_drive_semantic_ui_state(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        build_ui_data(generated, enhanced, output)

        modules_index = json.loads((output / "modules-index.json").read_text(encoding="utf-8"))
        problems_index = json.loads((output / "problems-index.json").read_text(encoding="utf-8"))
        search_index = json.loads((output / "search-index.json").read_text(encoding="utf-8"))
        modules = {module["name"]: module for module in modules_index["modules"]}
        alpha_verification = modules["alpha"]["verification"]
        beta_verification = modules["beta"]["verification"]
        alpha_search = next(
            record
            for record in search_index["records"]
            if record["entity_kind"] == "module" and record["title"] == "alpha"
        )

        self.assertEqual(alpha_verification["verdict"], "warning")
        self.assertEqual(alpha_verification["verification_status"], "verified_warning")
        self.assertEqual(alpha_verification["weak_claims_count"], 1)
        self.assertEqual(alpha_verification["unsupported_claims_count"], 0)
        self.assertEqual(alpha_verification["missing_factual_support_count"], 1)
        self.assertEqual(beta_verification["verdict"], "pass")
        self.assertEqual(beta_verification["weak_claims_count"], 0)
        self.assertEqual(problems_index["summary"]["weak_claims_total"], 1)
        self.assertEqual(problems_index["summary"]["unsupported_claims_total"], 0)
        self.assertEqual(problems_index["summary"]["modules_with_failures"], 0)
        self.assertIn("weak claims: 999", alpha_search["search_text"])
        self.assertIn("verdict: fail", alpha_search["search_text"])
        self.assertEqual(alpha_search["verification_verdict"], "warning")
        self.assertTrue(FORBIDDEN_MARKDOWN_SEMANTIC_FIELDS)
        self.assertNotIn("weak_claims_count", alpha_search)

    def test_problems_index_contains_module_and_issue_problems(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        build_ui_data(generated, enhanced, output)

        problems = json.loads((output / "problems-index.json").read_text(encoding="utf-8"))
        module_problem_types = {
            problem["module"]: set(problem["problem_types"]) for problem in problems["module_problems"]
        }
        issue_types = {(problem["module"], problem["issue_type"]) for problem in problems["issue_problems"]}

        self.assertEqual(problems["status"], "partial")
        self.assertEqual(problems["summary"]["modules_with_warnings"], 1)
        self.assertEqual(problems["summary"]["modules_missing_enhanced"], 1)
        self.assertEqual(problems["summary"]["modules_missing_verification"], 1)
        self.assertEqual(problems["summary"]["weak_claims_total"], 1)
        self.assertEqual(problems["summary"]["missing_factual_support_total"], 1)
        self.assertIn("verification_warning", module_problem_types["alpha"])
        self.assertIn("missing_enhanced", module_problem_types["missing"])
        self.assertIn("missing_verification", module_problem_types["missing"])
        self.assertIn(("alpha", "weak_claim"), issue_types)
        self.assertIn(("alpha", "missing_factual_support"), issue_types)
        first_issue = problems["issue_problems"][0]
        self.assertEqual(first_issue["module_path"], "/module/alpha")
        self.assertIn("verification_json_path", first_issue)

    def test_files_functions_and_search_indexes_are_built_from_normalized_sources(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        analysis = self.build_analysis_fixture(root)
        build_ui_data(generated, enhanced, output, analysis_root=analysis)

        files_index = json.loads((output / "files-index.json").read_text(encoding="utf-8"))
        functions_index = json.loads((output / "functions-index.json").read_text(encoding="utf-8"))
        search_index = json.loads((output / "search-index.json").read_text(encoding="utf-8"))

        files = {item["path"]: item for item in files_index["files"]}
        functions = {item["name"]: item for item in functions_index["functions"]}
        records = search_index["records"]

        self.assertIn("src/alpha.py", files)
        self.assertTrue(files["src/alpha.py"]["doc_path"].endswith("docs/generated/files/file-src-alpha-py.md"))
        self.assertEqual(files["src/alpha.py"]["module_names"], ["alpha"])
        self.assertIn("serve_alpha", functions)
        self.assertEqual(functions["serve_alpha"]["source_file"], "src/alpha.py")
        self.assertEqual(functions["serve_alpha"]["module_names"], ["alpha"])
        self.assertTrue(any(record["entity_kind"] == "module" and record["title"] == "alpha" for record in records))
        self.assertTrue(any(record["entity_kind"] == "file" and record["title"] == "alpha.py" for record in records))
        self.assertTrue(any(record["entity_kind"] == "function" and record["title"] == "serve_alpha" for record in records))
        self.assertTrue(any(record["entity_kind"] == "problem" and record["problem_type"] == "weak_claim" for record in records))
        self.assertTrue(any("Enhanced" in record["search_text"] for record in records if record["entity_kind"] == "module"))
        self.assert_no_backslashes(files_index)
        self.assert_no_backslashes(functions_index)
        self.assert_no_backslashes(search_index)

    def test_history_index_uses_history_indexes_and_paths_are_forward_slash(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        build_ui_data(generated, enhanced, output)

        history = json.loads((output / "history-index.json").read_text(encoding="utf-8"))
        self.assertEqual(history["generation_runs"][0]["run_id"], "generation-run")
        self.assertEqual(history["verification_runs"][0]["run_id"], "verification-run")
        self.assertEqual(history["verification_runs"][0]["verification_mode"], "same_context")
        self.assert_no_backslashes(history)

    def test_history_runs_contains_run_details_and_results(self) -> None:
        root = self.make_temp_dir()
        generated, enhanced, output = self.build_fixture(root)
        build_ui_data(generated, enhanced, output)

        history_runs = json.loads((output / "history-runs.json").read_text(encoding="utf-8"))
        generation_run = history_runs["generation_runs"][0]
        verification_run = history_runs["verification_runs"][0]

        self.assertEqual(generation_run["kind"], "generation")
        self.assertTrue(generation_run["latest_live_run"])
        self.assertEqual(generation_run["cache_hit_rate"], 0.5)
        self.assertEqual(generation_run["results"][0]["module"], "alpha")
        self.assertEqual(generation_run["results"][0]["status"], "generated")
        self.assertEqual(verification_run["kind"], "verification")
        self.assertTrue(verification_run["latest_live_run"])
        self.assertEqual(verification_run["verification_mode"], "same_context")
        self.assertEqual(verification_run["results"][0]["verdict"], "warning")
        self.assert_no_backslashes(history_runs)

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
