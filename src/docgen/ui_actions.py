from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ACTION_SCHEMA_VERSION = "1.0"
CONFIRMATION_PHRASE = "RUN"
ALLOWED_ACTIONS = {"build_ui_data", "explain_module", "verify_module"}


class ActionError(ValueError):
    pass


SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


class ActionRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        generated_root: Path,
        enhanced_root: Path,
        ui_data_root: Path,
        analysis_root: Path | None = None,
        subprocess_runner: SubprocessRunner = subprocess.run,
    ) -> None:
        self.project_root = project_root.resolve()
        self.generated_root = generated_root.resolve()
        self.enhanced_root = enhanced_root.resolve()
        self.ui_data_root = ui_data_root.resolve()
        self.analysis_root = (analysis_root or (self.project_root / ".docgen-analysis-live")).resolve()
        self.subprocess_runner = subprocess_runner
        self.lock = threading.Lock()
        self._log_lock = threading.Lock()

    @property
    def actions_root(self) -> Path:
        return self.enhanced_root / "actions"

    @property
    def logs_root(self) -> Path:
        return self.actions_root / "logs"

    @property
    def action_log_path(self) -> Path:
        return self.actions_root / "action-log.json"

    def preview(
        self,
        action_type: str,
        *,
        modules: list[str] | None = None,
        force: bool = False,
        allowed_modules: set[str] | None = None,
    ) -> dict[str, Any]:
        modules = normalize_modules(modules)
        self._validate_action(action_type, modules=modules, allowed_modules=allowed_modules, confirmed=True, preview=True)
        command = self.build_command(action_type, modules=modules, force=force)
        return {
            "schema_version": ACTION_SCHEMA_VERSION,
            "action_type": action_type,
            "targets": modules,
            "command": command,
            "network_may_be_used": action_type in {"explain_module", "verify_module"},
            "risk_class": "no_network_low_cost" if action_type == "build_ui_data" else "llm_targeted_cost",
            "confirmation_required": action_type in {"explain_module", "verify_module"},
            "confirmation_phrase": CONFIRMATION_PHRASE if action_type in {"explain_module", "verify_module"} else None,
            "expected_outputs": expected_outputs_for(action_type),
            "warnings": warnings_for(action_type, modules),
        }

    def run(
        self,
        action_type: str,
        *,
        modules: list[str] | None = None,
        force: bool = False,
        confirmed: bool = False,
        allowed_modules: set[str] | None = None,
    ) -> dict[str, Any]:
        modules = normalize_modules(modules)
        try:
            self._validate_action(action_type, modules=modules, allowed_modules=allowed_modules, confirmed=confirmed)
        except ActionError as exc:
            entry = self._base_entry(
                action_type=action_type if action_type in ALLOWED_ACTIONS else "unknown",
                targets=modules,
                command=[],
                status="rejected",
                warnings=[str(exc)],
            )
            entry["error"] = str(exc)
            self.append_action_log(entry)
            return entry

        command = self.build_command(action_type, modules=modules, force=force)
        if not self.lock.acquire(blocking=False):
            entry = self._base_entry(
                action_type=action_type,
                targets=modules,
                command=command,
                status="rejected",
                warnings=["Another action is already running."],
            )
            entry["error"] = "Another action is already running."
            self.append_action_log(entry)
            return entry

        action_id = make_action_id()
        started = time.monotonic()
        stdout_path = self.logs_root / f"{action_id}.stdout.txt"
        stderr_path = self.logs_root / f"{action_id}.stderr.txt"
        stdout_text = ""
        stderr_text = ""
        exit_code: int | None = None
        process_status = "failed"
        error: str | None = None
        parsed_result: dict[str, Any] | None = None
        domain = {
            "domain_status": "failed",
            "parsed_result_summary": None,
            "network_call": None,
        }
        try:
            result = self.subprocess_runner(
                command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            exit_code = int(result.returncode)
            parsed_result = parse_json_stdout(result.stdout or "")
            stdout_text = sanitize_log_text(result.stdout or "")
            stderr_text = sanitize_log_text(result.stderr or "")
            process_status = "success" if exit_code == 0 else "failed"
            domain = classify_domain_result(action_type, exit_code, parsed_result)
            if exit_code != 0:
                error = f"Command exited with code {exit_code}."
        except Exception as exc:  # pragma: no cover - exercised through subprocess failures in real use.
            exit_code = None
            stderr_text = sanitize_log_text(str(exc))
            error = str(exc)
            process_status = "failed"
            domain = {
                "domain_status": "failed",
                "parsed_result_summary": None,
                "network_call": None,
            }
        finally:
            self.lock.release()

        self.logs_root.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")
        duration = round(time.monotonic() - started, 3)
        entry = self._base_entry(
            action_id=action_id,
            action_type=action_type,
            targets=modules,
            command=command,
            status=str(domain["domain_status"]),
            warnings=warnings_for(action_type, modules),
        )
        entry.update(
            {
                "process_status": process_status,
                "domain_status": domain["domain_status"],
                "parsed_result_summary": domain["parsed_result_summary"],
                "network_call": domain["network_call"],
                "stdout_path": normalize_relative_path(stdout_path, self.project_root),
                "stderr_path": normalize_relative_path(stderr_path, self.project_root),
                "exit_code": exit_code,
                "duration_seconds": duration,
                "error": error,
            }
        )
        self.append_action_log(entry)
        return entry

    def build_command(self, action_type: str, *, modules: list[str], force: bool) -> list[str]:
        python = sys.executable
        generated = normalize_relative_path(self.generated_root, self.project_root)
        enhanced = normalize_relative_path(self.enhanced_root, self.project_root)
        ui_data = normalize_relative_path(self.ui_data_root, self.project_root)
        plan = normalize_relative_path(self.generated_root / "explain-plan.json", self.project_root)
        if action_type == "build_ui_data":
            command = [
                python,
                "-m",
                "docgen",
                "build-ui-data",
            ]
            command.extend(["--analysis", normalize_relative_path(self.analysis_root, self.project_root)])
            command.extend(["--generated", generated, "--enhanced", enhanced, "--output", ui_data])
            return command
        if action_type == "explain_module":
            command = [python, "-m", "docgen", "explain-batch", "--plan", plan, "--output", enhanced]
            for module in modules:
                command.extend(["--only-module", module])
            if force:
                command.append("--force")
            return command
        if action_type == "verify_module":
            command = [
                python,
                "-m",
                "docgen",
                "verify-batch",
                "--plan",
                plan,
                "--enhanced",
                enhanced,
                "--output",
                normalize_relative_path(self.enhanced_root / "verification", self.project_root),
            ]
            for module in modules:
                command.extend(["--only-module", module])
            if force:
                command.append("--force")
            return command
        raise ActionError(f"Unsupported action type: {action_type}")

    def load_action_log(self) -> dict[str, Any]:
        if not self.action_log_path.is_file():
            return {
                "schema_version": ACTION_SCHEMA_VERSION,
                "updated_at": None,
                "actions": [],
            }
        try:
            payload = json.loads(self.action_log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "schema_version": ACTION_SCHEMA_VERSION,
                "updated_at": None,
                "actions": [],
                "warnings": ["Action log is missing or invalid."],
            }
        if not isinstance(payload, dict):
            return {
                "schema_version": ACTION_SCHEMA_VERSION,
                "updated_at": None,
                "actions": [],
                "warnings": ["Action log root is invalid."],
            }
        actions = payload.get("actions")
        if not isinstance(actions, list):
            payload["actions"] = []
        return payload

    def append_action_log(self, entry: dict[str, Any]) -> None:
        with self._log_lock:
            self.actions_root.mkdir(parents=True, exist_ok=True)
            payload = self.load_action_log()
            actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
            actions = [entry, *actions]
            payload = {
                "schema_version": ACTION_SCHEMA_VERSION,
                "updated_at": utc_now(),
                "actions": actions,
            }
            self.action_log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _validate_action(
        self,
        action_type: str,
        *,
        modules: list[str],
        allowed_modules: set[str] | None,
        confirmed: bool,
        preview: bool = False,
    ) -> None:
        if action_type not in ALLOWED_ACTIONS:
            raise ActionError(f"Unsupported action type: {action_type}")
        if action_type in {"explain_module", "verify_module"}:
            if not modules:
                raise ActionError("At least one explicit module is required.")
            for module in modules:
                if "*" in module or "?" in module:
                    raise ActionError("Wildcards are not allowed in module names.")
                if allowed_modules is not None and module not in allowed_modules:
                    available = ", ".join(sorted(allowed_modules))
                    raise ActionError(f"Unknown module: {module}. Available modules: {available}")
            if not confirmed and not preview:
                raise ActionError(f"Confirmation phrase is required: {CONFIRMATION_PHRASE}")

    def _base_entry(
        self,
        *,
        action_type: str,
        targets: list[str],
        command: list[str],
        status: str,
        warnings: list[str],
        action_id: str | None = None,
    ) -> dict[str, Any]:
        action_id = action_id or make_action_id()
        return {
            "action_id": action_id,
            "created_at": utc_now(),
            "action_type": action_type,
            "targets": targets,
            "status": status,
            "process_status": status,
            "domain_status": status,
            "parsed_result_summary": None,
            "network_call": None,
            "network_may_be_used": action_type in {"explain_module", "verify_module"},
            "command": sanitize_command(command),
            "stdout_path": None,
            "stderr_path": None,
            "exit_code": None,
            "duration_seconds": 0.0,
            "warnings": warnings,
        }


def normalize_modules(modules: list[str] | None) -> list[str]:
    if not modules:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for module in modules:
        value = str(module or "").strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def expected_outputs_for(action_type: str) -> list[str]:
    if action_type == "build_ui_data":
        return [
            "docs/ui-data/current-state.json",
            "docs/ui-data/modules-index.json",
            "docs/ui-data/history-index.json",
            "docs/ui-data/problems-index.json",
            "docs/ui-data/search-index.json",
        ]
    if action_type == "explain_module":
        return [
            "docs/enhanced/modules/<module>.md",
            "docs/enhanced/llm-runs/<module>.metadata.json",
            "docs/enhanced/llm-batch-run-manifest.json",
            "docs/enhanced/history/generation/<run_id>.json",
        ]
    if action_type == "verify_module":
        return [
            "docs/enhanced/verification/<module>.verification.json",
            "docs/enhanced/verification/<module>.verification.md",
            "docs/enhanced/verification/llm-batch-verification-manifest.json",
            "docs/enhanced/history/verification/<run_id>.json",
        ]
    return []


def warnings_for(action_type: str, modules: list[str]) -> list[str]:
    if action_type == "build_ui_data":
        return ["This action mutates docs/ui-data and does not make network calls."]
    if action_type == "explain_module":
        return [
            "This targeted action may call the configured LLM provider and incur token cost.",
            f"Targets: {', '.join(modules)}",
            "The underlying explain-batch CLI owns generation current/history manifests.",
        ]
    if action_type == "verify_module":
        return [
            "This targeted action may call the configured LLM provider and incur token cost.",
            f"Targets: {', '.join(modules)}",
            "The underlying verify-batch CLI owns verification current/history manifests.",
        ]
    return []


def parse_json_stdout(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def classify_domain_result(action_type: str, exit_code: int | None, parsed: dict[str, Any] | None) -> dict[str, Any]:
    if action_type == "explain_module":
        summary = summarize_batch_result(parsed, kind="generation") if parsed else None
        if exit_code != 0:
            return domain_result("failed", summary, parsed)
        if summary is None:
            return domain_result("success", summary, parsed)
        if int_value(summary.get("failed_count")) > 0:
            return domain_result("failed", summary, parsed)
        if int_value(summary.get("generated_count")) > 0:
            return domain_result("success", summary, parsed)
        if (
            int_value(summary.get("skipped_by_plan_count")) > 0
            or int_value(summary.get("skipped_cached_count")) > 0
            or int_value(summary.get("total_modules_selected")) == 0
        ):
            return domain_result("no_op", summary, parsed)
        return domain_result("no_op", summary, parsed)
    if action_type == "verify_module":
        summary = summarize_batch_result(parsed, kind="verification") if parsed else None
        if exit_code != 0:
            return domain_result("failed", summary, parsed)
        if summary is None:
            return domain_result("success", summary, parsed)
        if int_value(summary.get("failed_count")) > 0:
            return domain_result("failed", summary, parsed)
        if int_value(summary.get("verified_count")) > 0:
            return domain_result("success", summary, parsed)
        if (
            int_value(summary.get("skipped_cached_count")) > 0
            or int_value(summary.get("skipped_missing_enhanced_count")) > 0
            or int_value(summary.get("total_modules_selected")) == 0
        ):
            return domain_result("no_op", summary, parsed)
        return domain_result("no_op", summary, parsed)
    if exit_code == 0:
        return domain_result("success", None, parsed)
    return domain_result("failed", None, parsed)


def summarize_batch_result(parsed: dict[str, Any] | None, *, kind: str) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    results = parsed.get("results") if isinstance(parsed.get("results"), list) else []
    module_statuses = []
    for result in results:
        if not isinstance(result, dict):
            continue
        module_statuses.append(
            {
                "module": sanitize_log_text(str(result.get("module") or "")),
                "status": sanitize_log_text(str(result.get("status") or "")),
                "error": sanitize_log_text(str(result.get("error") or "")) if result.get("error") else None,
            }
        )
    if kind == "generation":
        failed_count = count_failed_generation(parsed, module_statuses)
        return {
            "kind": kind,
            "selected_modules": sanitized_list(parsed.get("selected_modules")),
            "total_modules_selected": int_value(parsed.get("total_modules_selected")),
            "generated_count": int_value(parsed.get("generated_count")),
            "skipped_cached_count": int_value(parsed.get("skipped_cached_count")),
            "skipped_by_plan_count": int_value(parsed.get("skipped_by_plan_count")),
            "failed_count": failed_count,
            "network_call": parsed.get("network_call"),
            "module_statuses": module_statuses,
        }
    failed_count = count_failed_verification(parsed, module_statuses)
    return {
        "kind": kind,
        "selected_modules": sanitized_list(parsed.get("selected_modules")),
        "total_modules_selected": int_value(parsed.get("total_modules_selected")),
        "verified_count": int_value(parsed.get("verified_count")),
        "warning_count": int_value(parsed.get("warning_count")),
        "skipped_cached_count": int_value(parsed.get("skipped_cached_count")),
        "skipped_missing_enhanced_count": int_value(parsed.get("skipped_missing_enhanced_count")),
        "failed_preflight_count": int_value(parsed.get("failed_preflight_count")),
        "failed_verification_count": int_value(parsed.get("failed_verification_count")),
        "failed_count": failed_count,
        "network_call": parsed.get("network_call"),
        "module_statuses": module_statuses,
    }


def domain_result(status: str, summary: dict[str, Any] | None, parsed: dict[str, Any] | None) -> dict[str, Any]:
    network_call = None
    if summary and "network_call" in summary:
        network_call = summary.get("network_call")
    elif isinstance(parsed, dict):
        network_call = parsed.get("network_call")
    return {
        "domain_status": status,
        "parsed_result_summary": summary,
        "network_call": network_call,
    }


def count_failed_generation(parsed: dict[str, Any], module_statuses: list[dict[str, Any]]) -> int:
    explicit = (
        int_value(parsed.get("failed_count"))
        + int_value(parsed.get("failed_generation_count"))
        + int_value(parsed.get("failed_preflight_count"))
    )
    result_failures = sum(1 for item in module_statuses if str(item.get("status") or "").startswith("failed"))
    return max(explicit, result_failures)


def count_failed_verification(parsed: dict[str, Any], module_statuses: list[dict[str, Any]]) -> int:
    explicit = (
        int_value(parsed.get("failed_count"))
        + int_value(parsed.get("failed_preflight_count"))
        + int_value(parsed.get("failed_verification_count"))
    )
    result_failures = sum(1 for item in module_statuses if str(item.get("status") or "").startswith("failed_"))
    return max(explicit, result_failures)


def sanitized_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [sanitize_log_text(str(item)) for item in value]


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def make_action_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
        return relative.as_posix()
    except ValueError:
        return path.as_posix()


def sanitize_command(command: list[str]) -> list[str]:
    return [sanitize_log_text(part) for part in command]


def sanitize_log_text(text: str) -> str:
    redacted = text
    for env_name in ("OPENROUTER_API", "OPENROUTER_API_KEY"):
        value = os.environ.get(env_name)
        if value:
            redacted = redacted.replace(value, f"[redacted:{env_name}]")
    return redacted.replace("reasoning_details", "[redacted-reasoning]")
