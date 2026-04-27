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
from docgen.llm.module_verifier import verify_module  # noqa: E402
from docgen.llm.schemas import LLMCompletionResult  # noqa: E402


class ModuleVerifierCliTests(unittest.TestCase):
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

    def build_fixture(self, root: Path, *, include_metadata: bool = True) -> tuple[Path, Path, Path, Path]:
        docs_dir = root / "docs" / "generated"
        enhanced_dir = root / "docs" / "enhanced"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "modules").mkdir(exist_ok=True)
        (docs_dir / "files").mkdir(exist_ok=True)
        (docs_dir / "functions").mkdir(exist_ok=True)

        (docs_dir / "modules" / "module-package-llm.md").write_text(
            "# Модуль: llm\n\n## Тип\npackage\n\n## Сущности\nentity_count: 3\n",
            encoding="utf-8",
        )
        (docs_dir / "coverage-report.md").write_text("# Coverage\nmodule llm covered\n", encoding="utf-8")
        (docs_dir / "dependency-map.md").write_text(
            "# Dependencies\nllm -> openai\nllm -> json\n" + ("detail line\n" * 20),
            encoding="utf-8",
        )
        (docs_dir / "module-map.md").write_text("# Module map\nllm\n", encoding="utf-8")
        (docs_dir / "files" / "index.md").write_text("# File index\n", encoding="utf-8")
        (docs_dir / "files" / "file-src-docgen-llm-config-py.md").write_text(
            "# File: config.py\nOpenRouterConfig\n",
            encoding="utf-8",
        )
        (docs_dir / "functions" / "function-index.md").write_text("# Function index\n", encoding="utf-8")

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
            "context_policy": {
                "include_function_index": "summary_or_links_only",
            },
            "global_docs": {
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
                    "entity_count": 3,
                    "dependency_count": 2,
                    "priority": "high",
                    "explain_mode": "full",
                }
            ],
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
                ]
            },
            "verification_policy": {},
        }
        plan_path = docs_dir / "explain-plan.json"
        self.write_json(plan_path, plan)

        enhanced_path = enhanced_dir / "modules" / "module-package-llm.md"
        enhanced_path.parent.mkdir(parents=True, exist_ok=True)
        enhanced_path.write_text(build_enhanced_markdown(), encoding="utf-8")

        metadata_path = enhanced_dir / "llm-runs" / "module-package-llm.metadata.json"
        if include_metadata:
            self.write_json(
                metadata_path,
                {
                    "schema_version": "1.0",
                    "module": "llm",
                    "module_doc_path": "modules/module-package-llm.md",
                    "context_reduced": True,
                    "context_reduction": {
                        "retained_context_files": ["modules/module-package-llm.md", "coverage-report.md"],
                        "dropped_context_files": ["module-map.md"],
                        "truncated_context_files": [
                            {
                                "path": "dependency-map.md",
                                "truncation_mode": "char_limit",
                                "original_chars": 80,
                                "retained_chars": 40,
                                "original_estimated_tokens": 20,
                                "retained_estimated_tokens": 10,
                            }
                        ],
                        "context_fingerprint": "fixture-fingerprint",
                    },
                },
            )
        output_path = enhanced_dir / "verification" / "module-package-llm.verification.json"
        return plan_path, enhanced_path, output_path, metadata_path

    def test_verify_module_dry_run_does_not_require_key_or_network_and_uses_metadata_context(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)

        def fail_network(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("network access is not allowed in dry-run")

        with mock.patch.object(socket, "create_connection", side_effect=fail_network):
            with mock.patch.object(socket, "socket", side_effect=fail_network):
                exit_code, stdout, stderr = self.capture_main(
                    [
                        "verify-module",
                        "--plan",
                        str(plan_path),
                        "--module",
                        "llm",
                        "--enhanced",
                        str(enhanced_path),
                        "--output",
                        str(output_path),
                        "--dry-run",
                    ],
                    env={},
                    cwd=root,
                )
        self.assertEqual(exit_code, 0, msg=stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["network_call"])
        self.assertEqual(payload["context_source"], "generation_metadata")
        self.assertEqual(payload["context_fingerprint"], "fixture-fingerprint")
        self.assertEqual(payload["retry_policy"]["max_attempts"], 2)
        self.assertFalse(output_path.exists())

    def test_verify_module_falls_back_to_explain_plan_without_metadata(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root, include_metadata=False)

        result = verify_module(plan_path, "llm", enhanced_path, output_path, dry_run=True)

        self.assertEqual(result["context_source"], "explain_plan_fallback")
        self.assertTrue(result["context_fingerprint"])

    def test_verify_module_fallback_plan_mode_uses_plan_even_when_metadata_exists(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root, include_metadata=True)

        result = verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            dry_run=True,
            verification_mode="fallback_plan",
        )

        self.assertEqual(result["verification_mode"], "fallback_plan")
        self.assertEqual(result["context_source"], "explain_plan_fallback")

    def test_verify_module_module_not_found_reports_available_modules(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)

        exit_code, stdout, stderr = self.capture_main(
            [
                "verify-module",
                "--plan",
                str(plan_path),
                "--module",
                "missing",
                "--enhanced",
                str(enhanced_path),
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

    def test_verify_module_missing_enhanced_file_reports_error(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        enhanced_path.unlink()

        exit_code, stdout, stderr = self.capture_main(
            [
                "verify-module",
                "--plan",
                str(plan_path),
                "--module",
                "llm",
                "--enhanced",
                str(enhanced_path),
                "--output",
                str(output_path),
                "--dry-run",
            ],
            env={},
            cwd=root,
        )
        self.assertEqual(exit_code, 2)
        self.assertIn("Enhanced markdown does not exist", stderr)

    def test_verify_module_output_exists_requires_force(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("existing", encoding="utf-8")

        with self.assertRaises(ValueError):
            verify_module(
                plan_path,
                "llm",
                enhanced_path,
                output_path,
                provider=FakeProvider(build_valid_verification_json()),
            )

    def test_verify_module_parses_structured_json_and_writes_report_and_summary(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        provider = FakeProvider(
            build_valid_verification_json(secret="secret-key"),
            usage={"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
            reasoning_present=True,
            reasoning_details_present=True,
            api_key="secret-key",
        )

        result = verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            provider=provider,
            force=True,
        )

        summary_path = output_path.with_suffix(".md")
        report_text = output_path.read_text(encoding="utf-8")
        summary_text = summary_path.read_text(encoding="utf-8")
        report = json.loads(report_text)

        self.assertTrue(output_path.exists())
        self.assertTrue(summary_path.exists())
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(provider.last_kwargs.get("response_format", {}).get("type"), "json_schema")
        self.assertTrue(report["structured_output_valid"])
        self.assertEqual(report["verifier_status"], "ok")
        self.assertEqual(report["provider_status"]["attempts"], 1)
        self.assertFalse(report["provider_status"]["retry_used"])
        self.assertEqual(report["usage"], {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33})
        self.assertEqual(report["verdict"], "warning")
        self.assertIn("checks", report)
        self.assertIn("unsupported_claims", report)
        self.assertIn("weak_claims", report)
        self.assertIn("missing_uncertainty", report)
        self.assertIn("missing_factual_support", report)
        self.assertNotIn("secret-key", report_text)
        self.assertNotIn('"reasoning_details":', report_text)
        self.assertNotIn("reasoning_details", summary_text)
        self.assertTrue(all(ref in report["factual_context_files"] for ref in report["unsupported_claims"][0]["evidence_refs"]))
        self.assertIn("## Вердикт", summary_text)
        self.assertEqual(result["structured_output_valid"], True)

    def test_verify_module_empty_content_retries_once_and_records_status(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        provider = FakeProvider.sequence(
            [
                FakeProviderResponse("", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
                FakeProviderResponse(
                    build_valid_verification_json(),
                    usage={"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
                ),
            ]
        )

        result = verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            provider=provider,
            force=True,
        )
        report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(provider.call_count, 2)
        self.assertTrue(result["structured_output_valid"])
        self.assertEqual(report["verifier_status"], "ok")
        self.assertEqual(report["provider_status"]["attempts"], 2)
        self.assertEqual(report["provider_status"]["first_attempt_status"], "empty_content")
        self.assertEqual(report["provider_status"]["second_attempt_status"], "ok")
        self.assertTrue(report["provider_status"]["retry_used"])
        self.assertEqual(report["provider_status"]["retry_reason"], "empty_content")
        self.assertEqual(report["attempt_usage"][0]["status"], "empty_content")
        self.assertEqual(report["usage"], {"prompt_tokens": 18, "completion_tokens": 11, "total_tokens": 29})
        self.assertFalse(provider.calls[1]["reasoning_enabled"])
        self.assertEqual(provider.calls[1]["response_format"], {"type": "json_object"})

    def test_verify_module_invalid_json_writes_invalid_report(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)

        result = verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            provider=FakeProvider("not json"),
            force=True,
        )
        report = json.loads(output_path.read_text(encoding="utf-8"))
        summary_text = output_path.with_suffix(".md").read_text(encoding="utf-8")

        self.assertFalse(report["structured_output_valid"])
        self.assertTrue(report["parse_errors"])
        self.assertEqual(report["provider_status"]["attempts"], 2)
        self.assertEqual(report["provider_status"]["first_attempt_status"], "provider_response_mismatch")
        self.assertEqual(report["provider_status"]["second_attempt_status"], "provider_response_mismatch")
        self.assertTrue(report["provider_status"]["retry_used"])
        self.assertEqual(report["verifier_status"], "provider_response_mismatch")
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(result["structured_output_valid"], False)
        self.assertIn("structured verification не состоялась", summary_text)

    def test_verify_module_invalid_json_object_records_parse_failure_and_only_one_retry(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        provider = FakeProvider.sequence(
            [
                FakeProviderResponse('{"unsupported_claims": ]}'),
                FakeProviderResponse('{"unsupported_claims": ]}'),
                FakeProviderResponse(build_valid_verification_json()),
            ]
        )

        verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            provider=provider,
            force=True,
        )
        report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(provider.call_count, 2)
        self.assertFalse(report["structured_output_valid"])
        self.assertEqual(report["provider_status"]["first_attempt_status"], "parse_failure")
        self.assertEqual(report["provider_status"]["second_attempt_status"], "parse_failure")
        self.assertTrue(report["parse_errors"])

    def test_verify_module_parses_structured_content_when_message_content_is_empty(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        provider = FakeProvider.sequence(
            [
                FakeProviderResponse(
                    "",
                    structured_content=json.loads(build_valid_verification_json()),
                )
            ]
        )

        result = verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            provider=provider,
            force=True,
        )
        report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(provider.call_count, 1)
        self.assertTrue(result["structured_output_valid"])
        self.assertEqual(report["verifier_status"], "ok")
        self.assertEqual(report["provider_status"]["attempts"], 1)

    def test_verify_module_auth_error_writes_classified_failure_without_retry(self) -> None:
        root = self.make_temp_dir()
        plan_path, enhanced_path, output_path, _ = self.build_fixture(root)
        provider = RaisingProvider("OPENROUTER_API is not set. Add it to environment or .env.")

        result = verify_module(
            plan_path,
            "llm",
            enhanced_path,
            output_path,
            provider=provider,
            force=True,
        )
        report = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertFalse(result["structured_output_valid"])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(report["provider_status"]["attempts"], 1)
        self.assertEqual(report["provider_status"]["first_attempt_status"], "auth_error")
        self.assertFalse(report["provider_status"]["retry_used"])
        self.assertEqual(report["verifier_status"], "auth_error")


class FakeProvider:
    def __init__(
        self,
        content: str,
        *,
        usage: dict[str, int] | None = None,
        reasoning_present: bool = False,
        reasoning_details_present: bool = False,
        api_key: str | None = None,
    ) -> None:
        self.content = content
        self.usage = usage
        self.reasoning_present = reasoning_present
        self.reasoning_details_present = reasoning_details_present
        self.config = SimpleNamespace(api_key=api_key)
        self.call_count = 0
        self.last_kwargs: dict = {}
        self.calls: list[dict] = []
        self.responses: list[FakeProviderResponse] | None = None

    @classmethod
    def sequence(cls, responses: list["FakeProviderResponse"]) -> "FakeProvider":
        provider = cls("")
        provider.responses = list(responses)
        return provider

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.call_count += 1
        self.last_kwargs = dict(kwargs)
        self.calls.append(dict(kwargs))
        if self.responses is not None:
            response = self.responses[min(self.call_count - 1, len(self.responses) - 1)]
            content = response.content
            usage = response.usage
            reasoning_present = response.reasoning_present
            reasoning_details_present = response.reasoning_details_present
            structured_content = response.structured_content
        else:
            content = self.content
            usage = self.usage
            reasoning_present = self.reasoning_present
            reasoning_details_present = self.reasoning_details_present
            structured_content = None
        return LLMCompletionResult(
            provider="openrouter",
            model=kwargs.get("model", "google/gemma-4-26b-a4b-it"),
            content=content,
            reasoning="present" if reasoning_present else None,
            reasoning_details_present=reasoning_details_present,
            usage=usage,
            raw_response_type="FakeResponse",
            finish_reason="stop",
            error=None,
            structured_content=structured_content,
        )


class FakeProviderResponse:
    def __init__(
        self,
        content: str,
        *,
        usage: dict[str, int] | None = None,
        reasoning_present: bool = False,
        reasoning_details_present: bool = False,
        structured_content: dict | None = None,
    ) -> None:
        self.content = content
        self.usage = usage
        self.reasoning_present = reasoning_present
        self.reasoning_details_present = reasoning_details_present
        self.structured_content = structured_content


class RaisingProvider:
    def __init__(self, message: str, *, api_key: str | None = None) -> None:
        self.message = message
        self.config = SimpleNamespace(api_key=api_key)
        self.call_count = 0

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.call_count += 1
        raise ValueError(self.message)


def build_enhanced_markdown() -> str:
    return """# Модуль: llm

## Что известно
Факты: entity_count равен 3.

## Назначение
Можно предположить техническую роль.

## Как работает
Не удалось определить по предоставленным фактам.

## Контур взаимодействия
Факты: dependency-map.md.

## Ключевые функции
Не удалось определить по предоставленным фактам.

## Зависимости
Факты: openai.

## Что не удалось определить
Runtime-поведение.

## Уровень уверенности
Средний.

## Фактическая опора
- modules/module-package-llm.md
"""


def build_valid_verification_json(*, secret: str = "") -> str:
    return json.dumps(
        {
            "unsupported_claims": [
                {
                    "claim_text": f"модуль гарантирует результат {secret}",
                    "section": "Назначение",
                    "severity": "medium",
                    "reason": "В factual context нет такой гарантии.",
                    "evidence_refs": ["modules/module-package-llm.md", "src/docgen/llm/config.py"],
                    "excerpt": "модуль гарантирует результат",
                }
            ],
            "weak_claims": [
                {
                    "claim_text": "Можно предположить техническую роль.",
                    "section": "Назначение",
                    "reason": "Формулировка осторожная, но требует явной опоры.",
                    "suggested_rewrite": "Указать, что назначение не определено.",
                }
            ],
            "missing_uncertainty": [{"section": "Зависимости", "reason": "Не отмечена граница статических данных."}],
            "missing_factual_support": [{"section": "Контур взаимодействия", "reason": "Нужна ссылка на dependency-map.md."}],
            "supported_claims_sample": [
                {"claim_text": "entity_count равен 3", "evidence_refs": ["modules/module-package-llm.md"]}
            ],
            "recommended_fixes": [
                {
                    "type": "downgrade_confidence",
                    "target_section": "Назначение",
                    "instruction": "Снизить уверенность и добавить неопределенность.",
                }
            ],
            "verdict": "warning",
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    unittest.main()
