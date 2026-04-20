from __future__ import annotations

import io
import json
import os
import socket
import subprocess
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
from docgen.llm.config import (  # noqa: E402
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_API_ENV,
    OPENROUTER_BASE_URL,
    build_openrouter_config,
    get_openrouter_api_key,
    load_dotenv_file,
)
from docgen.llm.openrouter_provider import OpenRouterProvider  # noqa: E402


class LlmProviderCliTests(unittest.TestCase):
    def run_cli_subprocess(
        self,
        *args: str,
        extra_env: dict[str, str | None] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
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
            cwd=cwd or ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

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

    def make_fake_response(
        self,
        *,
        content: object = "docgen-openrouter-ok",
        include_reasoning: bool = False,
        include_reasoning_details: bool = False,
        usage: object | None = None,
        finish_reason: str = "stop",
    ) -> object:
        message = SimpleNamespace(content=content)
        if include_reasoning:
            message.reasoning = "synthetic-reasoning"
        if include_reasoning_details:
            message.reasoning_details = [{"type": "trace"}]
        choice = SimpleNamespace(message=message, finish_reason=finish_reason)
        return SimpleNamespace(choices=[choice], usage=usage)

    def test_config_reads_openrouter_api_env_var(self) -> None:
        with mock.patch.dict(os.environ, {OPENROUTER_API_ENV: "from-env"}, clear=True):
            self.assertEqual(get_openrouter_api_key(dotenv_path=self.make_temp_dir() / ".env"), "from-env")

    def test_config_does_not_use_openrouter_api_key_alias_as_primary_key(self) -> None:
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "wrong-name"}, clear=True):
            self.assertIsNone(get_openrouter_api_key(dotenv_path=self.make_temp_dir() / ".env"))

    def test_load_dotenv_file_reads_openrouter_api(self) -> None:
        dotenv_path = self.make_temp_dir() / ".env"
        dotenv_path.write_text("OPENROUTER_API=from-dotenv\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_dotenv_file(dotenv_path)
            self.assertEqual(loaded["OPENROUTER_API"], "from-dotenv")
            self.assertEqual(os.environ["OPENROUTER_API"], "from-dotenv")

    def test_load_dotenv_file_does_not_override_existing_env(self) -> None:
        dotenv_path = self.make_temp_dir() / ".env"
        dotenv_path.write_text("OPENROUTER_API=from-dotenv\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"OPENROUTER_API": "already-set"}, clear=True):
            load_dotenv_file(dotenv_path)
            self.assertEqual(os.environ["OPENROUTER_API"], "already-set")

    def test_build_openrouter_config_handles_missing_dotenv(self) -> None:
        missing_path = self.make_temp_dir() / ".env"
        with mock.patch.dict(os.environ, {}, clear=True):
            config = build_openrouter_config(dotenv_path=missing_path)
        self.assertEqual(config.model, DEFAULT_OPENROUTER_MODEL)
        self.assertFalse(config.key_present)

    def test_llm_smoke_dry_run_cli_does_not_touch_network_or_require_key(self) -> None:
        def fail_network(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("network access is not allowed in dry-run tests")

        with mock.patch.object(socket, "create_connection", side_effect=fail_network):
            with mock.patch.object(socket, "socket", side_effect=fail_network):
                exit_code, stdout, stderr = self.capture_main(
                    ["llm-smoke", "--provider", "openrouter", "--dry-run"],
                    env={},
                    cwd=self.make_temp_dir(),
                )
        self.assertEqual(exit_code, 0, msg=stderr)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["provider"], "openrouter")
        self.assertFalse(payload["key_present"])
        self.assertFalse(payload["network_call"])

    def test_llm_smoke_dry_run_output_does_not_contain_key_value(self) -> None:
        secret = "super-secret-token"
        exit_code, stdout, stderr = self.capture_main(
            ["llm-smoke", "--provider", "openrouter", "--dry-run"],
            env={OPENROUTER_API_ENV: secret},
            cwd=self.make_temp_dir(),
        )
        self.assertEqual(exit_code, 0, msg=stderr)
        self.assertNotIn(secret, stdout)
        self.assertNotIn(secret, stderr)
        self.assertIn('"key_present": true', stdout)

    def test_llm_smoke_live_without_key_reports_clear_error(self) -> None:
        exit_code, stdout, stderr = self.capture_main(
            ["llm-smoke", "--provider", "openrouter"],
            env={},
            cwd=self.make_temp_dir(),
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("OPENROUTER_API is not set. Add it to environment or .env.", stderr)

    def test_openrouter_provider_builds_client_with_base_url_and_default_model(self) -> None:
        captured: dict[str, str] = {}
        fake_response = self.make_fake_response()

        class FakeOpenAIClient:
            def __init__(self, *, base_url: str, api_key: str) -> None:
                captured["base_url"] = base_url
                captured["api_key"] = api_key
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(
                        create=self._create,
                    )
                )

            def _create(self, **kwargs):
                captured["model"] = kwargs["model"]
                return fake_response

        provider = OpenRouterProvider(
            config=build_openrouter_config(api_key="test-key"),
        )
        with mock.patch("docgen.llm.openrouter_provider.get_openai_client_class", return_value=FakeOpenAIClient):
            result = provider.complete([{"role": "user", "content": "ping"}])
        self.assertEqual(captured["base_url"], OPENROUTER_BASE_URL)
        self.assertEqual(captured["api_key"], "test-key")
        self.assertEqual(captured["model"], DEFAULT_OPENROUTER_MODEL)
        self.assertEqual(result.model, DEFAULT_OPENROUTER_MODEL)

    def test_complete_passes_reasoning_extra_body_when_enabled(self) -> None:
        captured: dict[str, object] = {}
        fake_client = build_fake_client(
            self.make_fake_response(),
            captured,
        )
        provider = OpenRouterProvider(
            config=build_openrouter_config(api_key="test-key"),
            client=fake_client,
        )
        provider.complete([{"role": "user", "content": "ping"}], reasoning_enabled=True)
        self.assertEqual(captured["extra_body"], {"reasoning": {"enabled": True}})

    def test_complete_omits_reasoning_extra_body_when_disabled(self) -> None:
        captured: dict[str, object] = {}
        fake_client = build_fake_client(
            self.make_fake_response(),
            captured,
        )
        provider = OpenRouterProvider(
            config=build_openrouter_config(api_key="test-key"),
            client=fake_client,
        )
        provider.complete([{"role": "user", "content": "ping"}], reasoning_enabled=False)
        self.assertNotIn("extra_body", captured)

    def test_complete_parses_content_without_reasoning_fields(self) -> None:
        fake_client = build_fake_client(self.make_fake_response(content="plain-content"))
        provider = OpenRouterProvider(
            config=build_openrouter_config(api_key="test-key"),
            client=fake_client,
        )
        result = provider.complete([{"role": "user", "content": "ping"}], reasoning_enabled=False)
        self.assertEqual(result.content, "plain-content")
        self.assertIsNone(result.reasoning)
        self.assertFalse(result.reasoning_details_present)

    def test_complete_sets_reasoning_details_and_usage_when_present(self) -> None:
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        fake_client = build_fake_client(
            self.make_fake_response(
                include_reasoning=True,
                include_reasoning_details=True,
                usage=usage,
            )
        )
        provider = OpenRouterProvider(
            config=build_openrouter_config(api_key="test-key"),
            client=fake_client,
        )
        result = provider.complete([{"role": "user", "content": "ping"}])
        self.assertEqual(result.reasoning, "synthetic-reasoning")
        self.assertTrue(result.reasoning_details_present)
        self.assertEqual(
            result.usage,
            {"completion_tokens": 7, "prompt_tokens": 11, "total_tokens": 18},
        )

    def test_provider_error_redacts_api_key(self) -> None:
        secret = "top-secret-key"
        fake_client = build_fake_client(error=RuntimeError(f"failure for {secret}"))
        provider = OpenRouterProvider(
            config=build_openrouter_config(api_key=secret),
            client=fake_client,
        )
        with self.assertRaises(ValueError) as exc_info:
            provider.complete([{"role": "user", "content": "ping"}])
        self.assertNotIn(secret, str(exc_info.exception))
        self.assertIn("[redacted]", str(exc_info.exception))

    def test_llm_smoke_dry_run_subprocess_works(self) -> None:
        temp_cwd = self.make_temp_dir()
        result = self.run_cli_subprocess(
            "llm-smoke",
            "--provider",
            "openrouter",
            "--dry-run",
            extra_env={OPENROUTER_API_ENV: None},
            cwd=temp_cwd,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["provider"], "openrouter")
        self.assertFalse(payload["network_call"])


def build_fake_client(
    response: object | None = None,
    captured: dict[str, object] | None = None,
    error: Exception | None = None,
) -> object:
    def create(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        if error is not None:
            raise error
        return response

    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )


if __name__ == "__main__":
    unittest.main()
