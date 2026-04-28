from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.ui_actions import ActionRunner, CONFIRMATION_PHRASE  # noqa: E402


class FakeSubprocessRunner:
    def __init__(self, *, returncode: int = 0, stdout: str = "ok", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command: list[str], **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


class UiActionsTests(unittest.TestCase):
    def make_root(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "docs" / "generated").mkdir(parents=True)
        (root / "docs" / "enhanced").mkdir(parents=True)
        (root / "docs" / "ui-data").mkdir(parents=True)
        (root / ".docgen-analysis-live").mkdir()
        return root

    def make_runner(self, root: Path, fake: FakeSubprocessRunner | None = None) -> ActionRunner:
        return ActionRunner(
            project_root=root,
            generated_root=root / "docs" / "generated",
            enhanced_root=root / "docs" / "enhanced",
            ui_data_root=root / "docs" / "ui-data",
            subprocess_runner=fake or FakeSubprocessRunner(),
        )

    def test_build_ui_data_action_runs_without_shell_and_writes_audit_log(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner(stdout="built")
        runner = self.make_runner(root, fake)

        entry = runner.run("build_ui_data", allowed_modules={"llm"})

        self.assertEqual(entry["status"], "success")
        self.assertEqual(len(fake.calls), 1)
        command, kwargs = fake.calls[0]
        self.assertIn("build-ui-data", command)
        self.assertFalse(kwargs.get("shell"))
        log = json.loads((root / "docs" / "enhanced" / "actions" / "action-log.json").read_text(encoding="utf-8"))
        self.assertEqual(log["actions"][0]["action_id"], entry["action_id"])
        self.assertTrue((root / entry["stdout_path"]).is_file())
        self.assertTrue((root / entry["stderr_path"]).is_file())

    def test_explain_action_rejects_missing_unknown_and_unconfirmed_module(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner()
        runner = self.make_runner(root, fake)

        missing = runner.run("explain_module", allowed_modules={"llm"}, confirmed=True)
        unknown = runner.run("explain_module", modules=["missing"], allowed_modules={"llm"}, confirmed=True)
        unconfirmed = runner.run("explain_module", modules=["llm"], allowed_modules={"llm"}, confirmed=False)

        self.assertEqual(missing["status"], "rejected")
        self.assertIn("At least one explicit module", missing["error"])
        self.assertEqual(unknown["status"], "rejected")
        self.assertIn("Unknown module", unknown["error"])
        self.assertEqual(unconfirmed["status"], "rejected")
        self.assertIn(CONFIRMATION_PHRASE, unconfirmed["error"])
        self.assertEqual(fake.calls, [])

    def test_verify_action_accepts_confirmed_known_module(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner(stdout="verified")
        runner = self.make_runner(root, fake)

        entry = runner.run("verify_module", modules=["llm"], force=True, confirmed=True, allowed_modules={"llm"})

        self.assertEqual(entry["status"], "success")
        command, _kwargs = fake.calls[0]
        self.assertIn("verify-batch", command)
        self.assertIn("--only-module", command)
        self.assertIn("llm", command)
        self.assertIn("--force", command)

    def test_explain_skipped_by_plan_stdout_is_no_op_not_success(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner(
            stdout=json.dumps(
                {
                    "selected_modules": [],
                    "total_modules_selected": 0,
                    "generated_count": 0,
                    "skipped_by_plan_count": 1,
                    "failed_count": 0,
                    "network_call": False,
                    "results": [
                        {
                            "module": "ui_actions",
                            "status": "skipped_by_plan",
                            "error": "explain_mode=skip; use --include-skip to include this module.",
                        }
                    ],
                }
            )
        )
        runner = self.make_runner(root, fake)

        entry = runner.run("explain_module", modules=["ui_actions"], confirmed=True, allowed_modules={"ui_actions"})

        self.assertEqual(entry["status"], "no_op")
        self.assertEqual(entry["domain_status"], "no_op")
        self.assertEqual(entry["process_status"], "success")
        self.assertFalse(entry["network_call"])
        summary = entry["parsed_result_summary"]
        self.assertEqual(summary["generated_count"], 0)
        self.assertEqual(summary["skipped_by_plan_count"], 1)
        self.assertEqual(summary["module_statuses"][0]["status"], "skipped_by_plan")
        log = json.loads((root / "docs" / "enhanced" / "actions" / "action-log.json").read_text(encoding="utf-8"))
        self.assertEqual(log["actions"][0]["parsed_result_summary"]["skipped_by_plan_count"], 1)

    def test_explain_generated_stdout_is_domain_success(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner(
            stdout=json.dumps(
                {
                    "selected_modules": ["llm"],
                    "total_modules_selected": 1,
                    "generated_count": 1,
                    "failed_count": 0,
                    "network_call": True,
                    "results": [{"module": "llm", "status": "generated", "error": None}],
                }
            )
        )
        runner = self.make_runner(root, fake)

        entry = runner.run("explain_module", modules=["llm"], confirmed=True, allowed_modules={"llm"})

        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["domain_status"], "success")
        self.assertTrue(entry["network_call"])
        self.assertEqual(entry["parsed_result_summary"]["generated_count"], 1)

    def test_explain_failed_stdout_is_domain_failed(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner(
            stdout=json.dumps(
                {
                    "selected_modules": ["llm"],
                    "total_modules_selected": 1,
                    "generated_count": 0,
                    "failed_count": 1,
                    "network_call": False,
                    "results": [{"module": "llm", "status": "failed_generation", "error": "provider error"}],
                }
            )
        )
        runner = self.make_runner(root, fake)

        entry = runner.run("explain_module", modules=["llm"], confirmed=True, allowed_modules={"llm"})

        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["domain_status"], "failed")
        self.assertEqual(entry["parsed_result_summary"]["failed_count"], 1)

    def test_verify_skipped_cached_stdout_is_no_op(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner(
            stdout=json.dumps(
                {
                    "selected_modules": ["llm"],
                    "total_modules_selected": 1,
                    "verified_count": 0,
                    "skipped_cached_count": 1,
                    "failed_count": 0,
                    "network_call": False,
                    "results": [{"module": "llm", "status": "skipped_cached", "error": None}],
                }
            )
        )
        runner = self.make_runner(root, fake)

        entry = runner.run("verify_module", modules=["llm"], confirmed=True, allowed_modules={"llm"})

        self.assertEqual(entry["status"], "no_op")
        self.assertEqual(entry["domain_status"], "no_op")
        self.assertFalse(entry["network_call"])
        self.assertEqual(entry["parsed_result_summary"]["skipped_cached_count"], 1)

    def test_arbitrary_action_and_wildcard_module_are_rejected(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner()
        runner = self.make_runner(root, fake)

        arbitrary = runner.run("rm_rf", allowed_modules={"llm"})
        wildcard = runner.run("verify_module", modules=["*"], confirmed=True, allowed_modules={"llm"})

        self.assertEqual(arbitrary["status"], "rejected")
        self.assertEqual(wildcard["status"], "rejected")
        self.assertEqual(fake.calls, [])

    def test_concurrent_action_lock_rejects_second_action(self) -> None:
        root = self.make_root()
        fake = FakeSubprocessRunner()
        runner = self.make_runner(root, fake)

        self.assertTrue(runner.lock.acquire(blocking=False))
        try:
            entry = runner.run("build_ui_data", allowed_modules={"llm"})
        finally:
            runner.lock.release()

        self.assertEqual(entry["status"], "rejected")
        self.assertIn("already running", entry["error"])
        self.assertEqual(fake.calls, [])

    def test_logs_redact_api_key_and_reasoning_details(self) -> None:
        root = self.make_root()
        old_value = os.environ.get("OPENROUTER_API")
        os.environ["OPENROUTER_API"] = "secret-key"
        self.addCleanup(self.restore_env, "OPENROUTER_API", old_value)
        fake = FakeSubprocessRunner(stdout="secret-key reasoning_details")
        runner = self.make_runner(root, fake)

        entry = runner.run("build_ui_data", allowed_modules={"llm"})
        stdout = (root / entry["stdout_path"]).read_text(encoding="utf-8")

        self.assertNotIn("secret-key", stdout)
        self.assertNotIn("reasoning_details", stdout)

    def restore_env(self, name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
