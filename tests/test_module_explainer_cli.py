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
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.cli import main  # noqa: E402
from docgen.llm.module_explainer import (  # noqa: E402
    build_module_context,
    explain_module,
    render_module_prompt,
    validate_enhanced_markdown,
    validate_semantic_markdown,
)
from docgen.llm.schemas import LLMCompletionResult  # noqa: E402


class ModuleExplainerCliTests(unittest.TestCase):
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
        environment = env or {}
        current_dir = Path.cwd()
        try:
            if cwd is not None:
                os.chdir(cwd)
            with mock.patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(argv)
        finally:
            os.chdir(current_dir)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def assert_context_reduction_sets_disjoint(self, context_reduction: dict) -> None:
        retained = set(context_reduction.get("retained_context_files", []))
        dropped = set(context_reduction.get("dropped_context_files", []))
        truncated = {
            item.get("path")
            for item in context_reduction.get("truncated_context_files", [])
            if isinstance(item, dict)
        }
        self.assertFalse(retained & dropped)
        self.assertFalse(retained & truncated)
        self.assertFalse(dropped & truncated)

    def build_plan_fixture(self, root: Path, *, large_file_doc: bool = False) -> tuple[Path, Path, Path]:
        docs_dir = root / "docs" / "generated"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "modules").mkdir(exist_ok=True)
        (docs_dir / "files").mkdir(exist_ok=True)
        (docs_dir / "functions").mkdir(exist_ok=True)

        module_doc = "# Модуль: llm\n\n## Тип\npackage\n\n## Ключевые файлы\n- config\n"
        file_doc = "# Файл: src/docgen/llm/config.py\n\n## Сущности\n| name |\n| --- |\n| build_openrouter_config |\n"
        if large_file_doc:
            file_doc += ("\n" + ("очень большой контекст " * 300))

        (docs_dir / "modules" / "module-package-llm.md").write_text(module_doc, encoding="utf-8")
        (docs_dir / "modules" / "module-test-skip.md").write_text("# Модуль: skip\n", encoding="utf-8")
        (docs_dir / "files" / "file-src-docgen-llm-config-py.md").write_text(file_doc, encoding="utf-8")
        (docs_dir / "files" / "index.md").write_text("# Индекс файлов\n", encoding="utf-8")
        (docs_dir / "functions" / "function-index.md").write_text("# Индекс функций\n", encoding="utf-8")
        (docs_dir / "dependency-map.md").write_text("# Карта зависимостей\n", encoding="utf-8")
        (docs_dir / "coverage-report.md").write_text("# Покрытие анализа\n", encoding="utf-8")
        (docs_dir / "module-map.md").write_text("# Карта модулей\n", encoding="utf-8")
        (docs_dir / "architecture.md").write_text("# Архитектура проекта\n", encoding="utf-8")

        plan_payload = {
            "schema_version": "1.0",
            "generated_at": "2026-04-20T00:00:00+00:00",
            "analysis_path": ".docgen-analysis-live",
            "docs_path": "docs/generated",
            "docs_manifest": {
                "path": "doc-manifest.json",
                "documentation_layout_version": "2.1",
                "generated_file_count": 8,
                "module_page_count": 2,
                "file_page_count": 1,
            },
            "model_plan": {
                "provider": "openrouter",
                "default_model": "google/gemma-4-26b-a4b-it",
                "reasoning_enabled": True,
                "reasoning_details_policy": "preserve_if_returned_later_do_not_render",
                "api_key_env": "OPENROUTER_API",
                "network_enabled_in_stage_3a": False,
            },
            "budget_policy": {
                "strategy": "module_first",
                "max_input_tokens_per_module": 24000,
                "max_output_tokens_per_module": 4000,
                "max_modules_per_run": 3,
                "token_estimation_method": "approx_chars_div_4",
                "token_estimates_are_exact": False,
            },
            "context_policy": {
                "allowed_context_roots": ["analysis_path", "docs_path"],
                "forbidden_context": ["source_files_direct_read", "readme_direct_read", "network"],
                "include_module_doc": True,
                "include_related_file_docs": True,
                "include_dependency_map": True,
                "include_coverage_report": True,
                "include_function_index": "summary_or_links_only",
            },
            "global_docs": {
                "architecture": {"path": "architecture.md", "exists": True},
                "coverage_report": {"path": "coverage-report.md", "exists": True},
                "dependency_map": {"path": "dependency-map.md", "exists": True},
                "file_index": {"path": "files/index.md", "exists": True},
                "function_index": {"path": "functions/function-index.md", "exists": True},
                "module_map": {"path": "module-map.md", "exists": True},
            },
            "modules": [
                {
                    "name": "llm",
                    "type": "package",
                    "module_page_role": "detailed",
                    "module_doc_path": "modules/module-package-llm.md",
                    "module_doc_exists": True,
                    "source_files": ["src/docgen/llm/config.py"],
                    "test_files": [],
                    "file_doc_paths": [
                        {
                            "source_file": "src/docgen/llm/config.py",
                            "doc_path": "files/file-src-docgen-llm-config-py.md",
                            "exists": True,
                        }
                    ],
                    "entity_count": 12,
                    "dependency_count": 8,
                    "estimated_input_tokens": 800,
                    "estimated_output_tokens": 1200,
                    "budget_status": "ok",
                    "priority": "high",
                    "explain_mode": "full",
                    "reasons": ["production-like detailed module"],
                    "warnings": [],
                    "missing_context": [],
                    "context_paths": [
                        "modules/module-package-llm.md",
                        "files/file-src-docgen-llm-config-py.md",
                        "dependency-map.md",
                        "coverage-report.md",
                        "files/index.md",
                        "functions/function-index.md",
                    ],
                    "context_reduction_reason": None,
                    "planned_output_path": "docs/enhanced/modules/module-package-llm.md",
                },
                {
                    "name": "skip-module",
                    "type": "test_asset",
                    "module_page_role": "test",
                    "module_doc_path": "modules/module-test-skip.md",
                    "module_doc_exists": True,
                    "source_files": [],
                    "test_files": ["tests/test_skip.py"],
                    "file_doc_paths": [],
                    "entity_count": 1,
                    "dependency_count": 0,
                    "estimated_input_tokens": 100,
                    "estimated_output_tokens": 200,
                    "budget_status": "ok",
                    "priority": "low",
                    "explain_mode": "skip",
                    "reasons": ["test module"],
                    "warnings": [],
                    "missing_context": [],
                    "context_paths": ["modules/module-test-skip.md"],
                    "context_reduction_reason": None,
                    "planned_output_path": "docs/enhanced/modules/module-test-skip.md",
                },
            ],
            "consistency": {
                "analysis_files_with_facts": ["src/docgen/llm/config.py"],
                "rendered_file_pages": ["src/docgen/llm/config.py"],
                "missing_file_pages": [],
                "analysis_render_mismatches": [],
                "stale_docs_detected": False,
                "manifest_has_file_pages": True,
                "manifest_has_module_pages": True,
            },
            "output_contract": {
                "module_explanation_required_sections": [
                    "Что известно",
                    "Назначение",
                    "Как работает",
                    "Контур взаимодействия",
                    "Ключевые функции",
                    "Зависимости",
                    "Что не удалось определить",
                    "Уровень уверенности",
                    "Фактическая опора",
                ],
                "forbidden_claims_without_support": [
                    "business_purpose",
                    "runtime_behavior",
                    "external_api_usage",
                ],
            },
            "verification_policy": {
                "require_factual_support_section": True,
                "require_uncertainty_section": True,
                "flag_unsupported_runtime_claims": True,
                "flag_business_claims_without_facts": True,
            },
            "warnings": [],
        }

        plan_path = docs_dir / "explain-plan.json"
        self.write_json(plan_path, plan_payload)
        source_file = root / "src" / "docgen" / "llm" / "config.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("SECRET_SOURCE_SHOULD_NOT_BE_READ = True\n", encoding="utf-8")
        return plan_path, docs_dir, source_file

    def test_explain_module_dry_run_does_not_require_key_or_network_and_finds_llm(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"

        def fail_network(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("network access is not allowed in dry-run")

        with mock.patch.object(socket, "create_connection", side_effect=fail_network):
            with mock.patch.object(socket, "socket", side_effect=fail_network):
                exit_code, stdout, stderr = self.capture_main(
                    [
                        "explain-module",
                        "--plan",
                        str(plan_path),
                        "--module",
                        "llm",
                        "--output",
                        str(output_path),
                        "--dry-run",
                    ],
                    env={},
                    cwd=root,
                )
        self.assertEqual(exit_code, 0, msg=stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["module"], "llm")
        self.assertFalse(payload["network_call"])
        self.assertFalse(output_path.exists())

    def test_explain_module_module_not_found_reports_available_modules(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "missing.md"

        exit_code, stdout, stderr = self.capture_main(
            [
                "explain-module",
                "--plan",
                str(plan_path),
                "--module",
                "missing-module",
                "--output",
                str(output_path),
                "--dry-run",
            ],
            env={},
            cwd=root,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Available modules", stderr)
        self.assertIn("llm", stderr)

    def test_explain_module_skip_mode_requires_force_skip_for_live_generation(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "skip.md"

        with self.assertRaises(ValueError):
            explain_module(
                plan_path,
                "skip-module",
                output_path,
                provider=FakeProvider(build_valid_markdown("skip-module")),
            )

    def test_explain_module_output_exists_requires_force(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("existing", encoding="utf-8")

        with self.assertRaises(ValueError):
            explain_module(
                plan_path,
                "llm",
                output_path,
                provider=FakeProvider(build_valid_markdown("llm")),
            )

    def test_build_module_context_reads_only_docs_generated_paths_and_not_source_files(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, source_file = self.build_plan_fixture(root)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")
        original_read_text = Path.read_text
        reads: list[str] = []

        def tracking_read_text(path_obj, *args, **kwargs):  # type: ignore[no-untyped-def]
            reads.append(str(path_obj).replace("\\", "/"))
            return original_read_text(path_obj, *args, **kwargs)

        with mock.patch.object(Path, "read_text", tracking_read_text):
            context = build_module_context(plan, module_target, max_input_tokens=24000, plan_path=plan_path)
        self.assertTrue(context["context_files"])
        self.assertFalse(any(str(source_file).replace("\\", "/") == item for item in reads))
        self.assertTrue(all(path.endswith(".md") for path in reads))

    def test_build_module_context_reduces_over_budget_context(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root, large_file_doc=True)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")

        context = build_module_context(plan, module_target, max_input_tokens=80, plan_path=plan_path)
        self.assertTrue(context["context_reduced"])
        self.assertIn("estimated_prompt_tokens_over_budget", context["context_warnings"])
        self.assertTrue(context["dropped_context_files"])
        self.assertTrue(context["retained_context_files"])
        self.assertTrue(context["token_budget"]["reduction_was_required"])
        self.assertTrue(context["token_budget"]["reduction_applied"])
        self.assertEqual(context["token_budget"]["reduction_reason"], "estimated_prompt_tokens_over_budget")
        self.assertGreater(
            context["token_budget"]["pre_reduction_estimated_prompt_tokens_with_margin"],
            context["token_budget"]["max_input_tokens"],
        )
        self.assertIn("context_fingerprint", context["context_reduction"])
        self.assert_context_reduction_sets_disjoint(context["context_reduction"])

    def test_build_module_context_keeps_empty_reduction_details_when_not_reduced(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")

        context = build_module_context(plan, module_target, max_input_tokens=24000, plan_path=plan_path)
        token_budget = context["token_budget"]
        context_reduction = context["context_reduction"]

        self.assertFalse(token_budget["reduction_was_required"])
        self.assertFalse(token_budget["reduction_applied"])
        self.assertIsNone(token_budget["reduction_reason"])
        self.assertEqual(context_reduction["dropped_context_files"], [])
        self.assertEqual(context_reduction["truncated_context_files"], [])
        self.assert_context_reduction_sets_disjoint(context_reduction)

    def test_reduction_reason_over_budget_requires_pre_reduction_budget_exceed(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root, large_file_doc=True)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")

        context = build_module_context(plan, module_target, max_input_tokens=3000, plan_path=plan_path)
        token_budget = context["token_budget"]

        self.assertEqual(token_budget["reduction_reason"], "estimated_prompt_tokens_over_budget")
        self.assertGreater(
            token_budget["pre_reduction_estimated_prompt_tokens_with_margin"],
            token_budget["max_input_tokens"],
        )

    def test_context_reduction_records_truncated_files_and_stable_fingerprint(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root, large_file_doc=True)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")

        first = build_module_context(plan, module_target, max_input_tokens=3000, plan_path=plan_path)
        second = build_module_context(plan, module_target, max_input_tokens=3000, plan_path=plan_path)
        truncated = first["context_reduction"]["truncated_context_files"]

        self.assertTrue(truncated)
        self.assertEqual(
            first["context_reduction"]["context_fingerprint"],
            second["context_reduction"]["context_fingerprint"],
        )
        self.assertLess(truncated[0]["retained_chars"], truncated[0]["original_chars"])
        self.assert_context_reduction_sets_disjoint(first["context_reduction"])

    def test_estimated_prompt_tokens_include_prompt_overhead(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")

        context = build_module_context(plan, module_target, max_input_tokens=24000, plan_path=plan_path)
        token_budget = context["token_budget"]
        self.assertGreater(token_budget["estimated_prompt_tokens"], token_budget["estimated_context_tokens"])
        self.assertGreater(token_budget["prompt_safety_margin_tokens"], 0)

    def test_render_module_prompt_contains_required_sections_and_unsupported_claim_guards(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        module_target = next(module for module in plan["modules"] if module["name"] == "llm")
        context = build_module_context(plan, module_target, max_input_tokens=24000, plan_path=plan_path)

        system_prompt, user_prompt = render_module_prompt(plan, module_target, context)
        self.assertIn("## Что известно", user_prompt)
        self.assertIn("## Фактическая опора", user_prompt)
        self.assertIn("business_purpose", user_prompt)
        self.assertIn("runtime", system_prompt)
        self.assertIn("Факты", user_prompt)
        self.assertIn("Интерпретации", user_prompt)
        self.assertIn("Неизвестно", user_prompt)

    def test_explain_module_live_writes_markdown_and_metadata_without_reasoning_details_or_api_key(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        provider = FakeProvider(
            build_valid_markdown("llm") + "\nreasoning_details\nsecret-key\nOPENROUTER_API=value\n",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            reasoning_present=True,
            reasoning_details_present=True,
            api_key="secret-key",
        )

        result = explain_module(
            plan_path,
            "llm",
            output_path,
            provider=provider,
            force=True,
        )
        metadata_path = Path(result["metadata_path"])
        markdown_text = output_path.read_text(encoding="utf-8")
        metadata_text = metadata_path.read_text(encoding="utf-8")
        metadata = json.loads(metadata_text)

        self.assertTrue(output_path.exists())
        self.assertTrue(metadata_path.exists())
        self.assertNotIn("reasoning_details", markdown_text)
        self.assertNotIn("secret-key", markdown_text)
        self.assertNotIn("OPENROUTER_API=", markdown_text)
        self.assertNotIn("secret-key", metadata_text)
        self.assertNotIn('"reasoning_details":', metadata_text)
        self.assertEqual(metadata["usage"], {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        self.assertIs(metadata["reasoning_present"], True)
        self.assertIs(metadata["reasoning_details_present"], True)
        self.assertIn("token_budget", metadata)
        self.assertIn("pre_reduction_estimated_prompt_tokens_with_margin", metadata["token_budget"])
        self.assertIn("post_reduction_estimated_prompt_tokens_with_margin", metadata["token_budget"])
        self.assertIn("context_reduction", metadata)
        self.assertIn("semantic_validation", metadata)

    def test_dry_run_and_live_use_same_reduction_plan(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root, large_file_doc=True)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"

        dry_run = explain_module(
            plan_path,
            "llm",
            output_path,
            dry_run=True,
            max_input_tokens=3000,
        )
        live = explain_module(
            plan_path,
            "llm",
            output_path,
            provider=FakeProvider(build_valid_markdown("llm")),
            force=True,
            max_input_tokens=3000,
        )
        metadata = json.loads(Path(live["metadata_path"]).read_text(encoding="utf-8"))

        self.assertFalse(dry_run["network_call"])
        self.assertIn("context_fingerprint", dry_run)
        self.assertEqual(
            dry_run["context_reduction"]["context_fingerprint"],
            metadata["context_reduction"]["context_fingerprint"],
        )
        self.assertEqual(
            dry_run["context_reduction"]["dropped_context_files"],
            metadata["context_reduction"]["dropped_context_files"],
        )
        self.assertEqual(
            dry_run["context_reduction"]["truncated_context_files"],
            metadata["context_reduction"]["truncated_context_files"],
        )
        self.assertEqual(
            dry_run["token_budget"]["reduction_was_required"],
            metadata["token_budget"]["reduction_was_required"],
        )
        self.assertEqual(
            dry_run["token_budget"]["reduction_applied"],
            metadata["token_budget"]["reduction_applied"],
        )

    def test_validate_enhanced_markdown_detects_missing_sections(self) -> None:
        validation = validate_enhanced_markdown("# Модуль: llm\n\n## Что известно\n", module_name="llm")
        self.assertFalse(validation["required_sections_present"])
        self.assertIn("## Назначение", validation["missing_sections"])

    def test_explain_module_live_saves_validation_warnings_when_sections_are_missing(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        provider = FakeProvider("# Модуль: llm\n\n## Что известно\n")

        result = explain_module(
            plan_path,
            "llm",
            output_path,
            provider=provider,
            force=True,
        )
        metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
        self.assertFalse(metadata["markdown_validation"]["required_sections_present"])
        self.assertIn("## Назначение", metadata["markdown_validation"]["missing_sections"])

    def test_explain_module_records_prompt_budget_exceeded_when_actual_usage_is_higher(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        provider = FakeProvider(
            build_valid_markdown("llm"),
            usage={"prompt_tokens": 26000, "completion_tokens": 20, "total_tokens": 26020},
        )

        result = explain_module(
            plan_path,
            "llm",
            output_path,
            provider=provider,
            force=True,
        )
        metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
        self.assertTrue(metadata["token_budget"]["prompt_budget_exceeded"])
        self.assertEqual(metadata["token_budget"]["actual_prompt_tokens"], 26000)

    def test_explain_module_blocks_live_call_without_allow_over_budget(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root, large_file_doc=True)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        provider = FakeProvider(build_valid_markdown("llm"))

        with self.assertRaises(ValueError):
            explain_module(
                plan_path,
                "llm",
                output_path,
                provider=provider,
                force=True,
                max_input_tokens=100,
            )
        self.assertEqual(provider.call_count, 0)

    def test_explain_module_allows_live_call_over_budget_with_explicit_flag(self) -> None:
        root = self.make_temp_dir()
        plan_path, _, _ = self.build_plan_fixture(root, large_file_doc=True)
        output_path = root / "docs" / "enhanced" / "modules" / "module-package-llm.md"
        provider = FakeProvider(build_valid_markdown("llm"))

        result = explain_module(
            plan_path,
            "llm",
            output_path,
            provider=provider,
            force=True,
            max_input_tokens=100,
            allow_over_budget=True,
        )
        metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(metadata["context_reduced"])
        self.assertTrue(metadata["dropped_context_files"])
        self.assertTrue(metadata["token_budget"]["override_used"])

    def test_validate_semantic_markdown_finds_overconfident_claims(self) -> None:
        validation = validate_semantic_markdown(
            """# Модуль: llm

## Что известно
Факты: структура файла.
Интерпретации: Модуль предназначен для работы с внешним API.
Неизвестно: нет данных.

## Назначение
Факты: нет данных.
Интерпретации: модуль отвечает за интеграцию.
Неизвестно: нет данных.

## Как работает
Факты: нет данных.
Интерпретации: выполняет оркестрацию.
Неизвестно: нет данных.

## Контур взаимодействия
Факты: нет данных.
Интерпретации: использует внешний API.
Неизвестно: нет данных.

## Ключевые функции
Факты: нет данных.
Интерпретации: гарантирует стабильный вывод.
Неизвестно: нет данных.

## Зависимости
Факты: нет данных.
Интерпретации: нет данных.
Неизвестно: нет данных.

## Что не удалось определить
нет данных

## Уровень уверенности
низкий

## Фактическая опора
- docs/generated/modules/module-package-llm.md
"""
        )
        self.assertTrue(validation["overconfident_claims"])
        self.assertEqual(validation["verdict"], "warning")

    def test_validate_semantic_markdown_requires_uncertainty_and_factual_support(self) -> None:
        validation = validate_semantic_markdown(
            """# Модуль: llm

## Что известно
Факты: структура.
Интерпретации: описание.
Неизвестно: нет данных.

## Назначение
Факты: структура.
Интерпретации: описание.
Неизвестно: нет данных.

## Как работает
Факты: структура.
Интерпретации: описание.
Неизвестно: нет данных.

## Контур взаимодействия
Факты: структура.
Интерпретации: описание.
Неизвестно: нет данных.

## Ключевые функции
Факты: структура.
Интерпретации: описание.
Неизвестно: нет данных.

## Зависимости
Факты: структура.
Интерпретации: описание.
Неизвестно: нет данных.

## Уровень уверенности
высокий
"""
        )
        self.assertFalse(validation["has_uncertainty_language"])
        self.assertFalse(validation["factual_support_section_present"])
        self.assertEqual(validation["verdict"], "fail")

    def test_validate_semantic_markdown_warns_on_bad_entity_count_wording(self) -> None:
        validation = validate_semantic_markdown(
            """# Модуль: llm

## Что известно
Факты: entity_count равен 12.
Интерпретации: нет.
Неизвестно: точное количество всех существующих сущностей неизвестно.

## Назначение
Факты: нет данных.
Интерпретации: нет.
Неизвестно: Не удалось определить по предоставленным фактам.

## Как работает
Факты: нет данных.
Интерпретации: нет.
Неизвестно: Не удалось определить по предоставленным фактам.

## Контур взаимодействия
Факты: нет данных.
Интерпретации: нет.
Неизвестно: Не удалось определить по предоставленным фактам.

## Ключевые функции
Факты: нет данных.
Интерпретации: нет.
Неизвестно: Не удалось определить по предоставленным фактам.

## Зависимости
Факты: нет данных.
Интерпретации: нет.
Неизвестно: Не удалось определить по предоставленным фактам.

## Что не удалось определить
Не удалось определить по предоставленным фактам.

## Уровень уверенности
Низкий.

## Фактическая опора
- modules/module-package-llm.md
""",
            entity_count=12,
        )
        self.assertTrue(validation["entity_count_wording_warnings"])
        self.assertEqual(validation["verdict"], "warning")


class FakeProvider:
    def __init__(
        self,
        markdown: str,
        *,
        usage: dict[str, int] | None = None,
        reasoning_present: bool = False,
        reasoning_details_present: bool = False,
        api_key: str | None = None,
    ) -> None:
        self.markdown = markdown
        self.usage = usage
        self.reasoning_present = reasoning_present
        self.reasoning_details_present = reasoning_details_present
        self.config = SimpleNamespace(api_key=api_key)
        self.call_count = 0

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.call_count += 1
        return LLMCompletionResult(
            provider="openrouter",
            model=kwargs.get("model", "google/gemma-4-26b-a4b-it"),
            content=self.markdown,
            reasoning="present" if self.reasoning_present else None,
            reasoning_details_present=self.reasoning_details_present,
            usage=self.usage,
            raw_response_type="FakeResponse",
            finish_reason="stop",
            error=None,
        )


def build_valid_markdown(module_name: str) -> str:
    return f"""# Модуль: {module_name}

## Что известно
Факты:
- Факты взяты из предоставленного factual context.
Интерпретации:
- По предоставленным фактам видно только структурные сведения.
Неизвестно:
- Точное бизнес-назначение не удалось определить по предоставленным фактам.

## Назначение
Факты:
- В factual markdown нет прямого описания назначения.
Интерпретации:
- Можно предположить только техническую роль, но это требует ручной проверки.
Неизвестно:
- Не удалось определить по предоставленным фактам.

## Как работает
Факты:
- Описание ограничено структурными данными из markdown-контекста.
Интерпретации:
- По предоставленным фактам видно только последовательность структурных шагов.
Неизвестно:
- Runtime-поведение не удалось определить по предоставленным фактам.

## Контур взаимодействия
Факты:
- Модуль связан с зависимостями и file pages из explain-plan.
Интерпретации:
- Можно предположить интеграцию с соседними factual pages.
Неизвестно:
- Побочные эффекты требуют ручной проверки.

## Ключевые функции
Факты:
- В контексте есть только ссылки на file pages и function index.
Интерпретации:
- Можно предположить наличие ключевых функций, но список неполный.
Неизвестно:
- Не удалось определить по предоставленным фактам.

## Зависимости
Факты:
- Зависимости перечислены в dependency map.
Интерпретации:
- По предоставленным фактам видно только статические зависимости.
Неизвестно:
- Runtime-generated dependencies не удалось определить по предоставленным фактам.

## Что не удалось определить
Не удалось определить по предоставленным фактам.

## Уровень уверенности
Средний: вывод ограничен factual markdown и explain-plan.

## Фактическая опора
- modules/module-package-llm.md
- files/file-src-docgen-llm-config-py.md
"""


if __name__ == "__main__":
    unittest.main()
