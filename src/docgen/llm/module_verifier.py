from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, prompts
from .module_explainer import (
    build_context_fingerprint,
    build_function_index_summary,
    build_module_context,
    estimate_tokens,
    load_context_document,
    load_explain_plan,
    metadata_output_path,
    render_context_documents,
    resolve_docs_root,
    select_module_target,
    truncate_context_content,
    unique_strings,
    validate_enhanced_markdown,
)
from .openrouter_provider import OpenRouterProvider

ATTEMPT_STATUSES = {
    "ok",
    "empty_content",
    "schema_rejected",
    "structured_output_invalid",
    "parse_failure",
    "provider_response_mismatch",
    "api_error",
    "auth_error",
    "network_error",
    "skipped",
}
RETRYABLE_ATTEMPT_STATUSES = {
    "empty_content",
    "schema_rejected",
    "structured_output_invalid",
    "parse_failure",
    "provider_response_mismatch",
}
VERIFICATION_REQUIRED_LIST_FIELDS = (
    "unsupported_claims",
    "weak_claims",
    "missing_uncertainty",
    "missing_factual_support",
    "supported_claims_sample",
    "recommended_fixes",
)
VERIFICATION_VERDICTS = {"pass", "warning", "fail"}
DEFAULT_VERIFICATION_MAX_OUTPUT_TOKENS = 1600
FALLBACK_VERIFICATION_MAX_OUTPUT_TOKENS = 1000


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    attempts: int
    first_attempt_status: str
    second_attempt_status: str | None
    retry_used: bool
    retry_reason: str | None
    final_status: str


@dataclass(frozen=True, slots=True)
class VerificationChecks:
    required_sections_present: bool
    factual_support_section_present: bool
    uncertainty_section_present: bool
    contains_reasoning_details: bool
    contains_api_key_leak: bool


@dataclass(frozen=True, slots=True)
class VerificationClaimIssue:
    claim_text: str = ""
    section: str = ""
    severity: str = ""
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    excerpt: str = ""
    suggested_rewrite: str = ""


@dataclass(frozen=True, slots=True)
class VerificationFix:
    type: str = ""
    target_section: str = ""
    instruction: str = ""


@dataclass(frozen=True, slots=True)
class VerificationReport:
    schema_version: str
    generated_at: str
    module: str
    provider: str
    model: str
    verification_mode: str
    context_source: str
    context_fingerprint: str
    enhanced_markdown_path: str
    factual_context_files: list[str]
    usage: dict[str, Any] | None
    usage_first_attempt: dict[str, Any] | None
    usage_second_attempt: dict[str, Any] | None
    attempt_usage: list[dict[str, Any]]
    reasoning_present: bool
    reasoning_details_present: bool
    structured_output_valid: bool
    parse_errors: list[str]
    provider_status: dict[str, Any]
    verifier_status: str
    checks: dict[str, Any]
    unsupported_claims: list[dict[str, Any]]
    weak_claims: list[dict[str, Any]]
    missing_uncertainty: list[dict[str, Any]]
    missing_factual_support: list[dict[str, Any]]
    supported_claims_sample: list[dict[str, Any]]
    recommended_fixes: list[dict[str, Any]]
    verdict: str


def verify_module(
    plan_path: Path,
    module_name: str,
    enhanced_path: Path,
    output_path: Path,
    *,
    provider_name: str = "openrouter",
    model: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    reasoning: bool | None = None,
    max_output_tokens: int = DEFAULT_VERIFICATION_MAX_OUTPUT_TOKENS,
    temperature: float = 0.0,
    verification_mode: str = "same_context",
    provider: OpenRouterProvider | None = None,
) -> dict[str, Any]:
    if verification_mode not in {"same_context", "fallback_plan"}:
        raise ValueError("verification-mode must be one of: same_context, fallback_plan.")
    if provider_name != "openrouter":
        raise ValueError(f"Unsupported provider: {provider_name}")
    if not enhanced_path.exists() or not enhanced_path.is_file():
        raise FileNotFoundError(f"Enhanced markdown does not exist: {enhanced_path}")

    summary_path = verification_summary_path(output_path)
    if not dry_run and not force:
        existing = [path for path in (output_path, summary_path) if path.exists()]
        if existing:
            paths = ", ".join(path.as_posix() for path in existing)
            raise ValueError(f"Verification output already exists: {paths}. Use --force to overwrite it.")

    plan = load_explain_plan(plan_path)
    module_target = select_module_target(plan, module_name)
    resolved_model = model or str(plan.get("model_plan", {}).get("default_model") or config.DEFAULT_MODEL)
    reasoning_enabled = bool(reasoning) if reasoning is not None else False
    enhanced_text = enhanced_path.read_text(encoding="utf-8")
    generation_metadata = load_generation_metadata(enhanced_path)
    verification_context = build_verification_context(
        plan,
        module_target,
        plan_path,
        generation_metadata=generation_metadata,
        verification_mode=verification_mode,
    )
    system_prompt, user_prompt = render_verification_prompt(
        module_target,
        enhanced_text,
        verification_context,
        fallback=False,
    )
    estimated_input_tokens = estimate_tokens(system_prompt + "\n" + user_prompt)

    dry_run_payload = {
        "module": module_target["name"],
        "provider": provider_name,
        "model": resolved_model,
        "verification_mode": verification_mode,
        "context_source": verification_context["context_source"],
        "context_fingerprint": verification_context["context_fingerprint"],
        "estimated_input_tokens": estimated_input_tokens,
        "max_output_tokens": max_output_tokens,
        "factual_context_files": verification_context["context_files"],
        "enhanced_markdown_path": enhanced_path.as_posix(),
        "context_reduced": verification_context["context_reduced"],
        "retry_policy": retry_policy(max_output_tokens),
        "network_call": False,
    }
    if dry_run:
        return dry_run_payload

    provider_instance = provider or OpenRouterProvider(
        config.build_openrouter_config(
            model=resolved_model,
            reasoning_enabled=reasoning_enabled,
        )
    )
    provider_api_key = getattr(getattr(provider_instance, "config", None), "api_key", None)

    first_attempt = run_verification_attempt(
        provider_instance,
        system_prompt,
        user_prompt,
        model=resolved_model,
        reasoning_enabled=reasoning_enabled,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        response_format=verification_response_format(),
        attempt_number=1,
    )
    attempts = [first_attempt]
    retry_used = False
    retry_reason: str | None = None

    if first_attempt["status"] in RETRYABLE_ATTEMPT_STATUSES:
        retry_used = True
        retry_reason = first_attempt["status"]
        fallback_system_prompt, fallback_user_prompt = render_verification_prompt(
            module_target,
            enhanced_text,
            verification_context,
            fallback=True,
        )
        attempts.append(
            run_verification_attempt(
                provider_instance,
                fallback_system_prompt,
                fallback_user_prompt,
                model=resolved_model,
                reasoning_enabled=False,
                max_output_tokens=min(max_output_tokens, FALLBACK_VERIFICATION_MAX_OUTPUT_TOKENS),
                temperature=0.0,
                response_format={"type": "json_object"},
                attempt_number=2,
            )
        )

    final_attempt = attempts[-1]
    provider_status = build_provider_status(attempts, retry_used=retry_used, retry_reason=retry_reason)
    report = build_verification_report(
        final_attempt.get("parsed_payload"),
        parse_errors=list(final_attempt.get("parse_errors") or []),
        validation_errors=list(final_attempt.get("validation_errors") or []),
        plan=plan,
        module_target=module_target,
        enhanced_path=enhanced_path,
        verification_context=verification_context,
        verification_mode=verification_mode,
        provider_name=final_attempt.get("provider") or provider_name,
        model=final_attempt.get("model") or resolved_model,
        usage=merge_usage([attempt.get("usage") for attempt in attempts]),
        usage_first_attempt=attempts[0].get("usage"),
        usage_second_attempt=attempts[1].get("usage") if len(attempts) > 1 else None,
        attempt_usage=[
            {"attempt": attempt["attempt"], "status": attempt["status"], "usage": attempt.get("usage")}
            for attempt in attempts
        ],
        reasoning_present=any(bool(attempt.get("reasoning_present")) for attempt in attempts),
        reasoning_details_present=any(bool(attempt.get("reasoning_details_present")) for attempt in attempts),
        provider_status=provider_status,
        api_key=provider_api_key,
        enhanced_text=enhanced_text,
    )
    summary = render_verification_summary(report)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary, encoding="utf-8", newline="\n")

    return {
        **dry_run_payload,
        "network_call": True,
        "output_path": output_path.as_posix(),
        "summary_path": summary_path.as_posix(),
        "usage": report["usage"],
        "usage_first_attempt": report["usage_first_attempt"],
        "usage_second_attempt": report["usage_second_attempt"],
        "reasoning_present": report["reasoning_present"],
        "reasoning_details_present": report["reasoning_details_present"],
        "structured_output_valid": report["structured_output_valid"],
        "parse_errors": report["parse_errors"],
        "provider_status": report["provider_status"],
        "verifier_status": report["verifier_status"],
        "verdict": report["verdict"],
        "unsupported_claims": len(report["unsupported_claims"]),
        "weak_claims": len(report["weak_claims"]),
        "missing_uncertainty": len(report["missing_uncertainty"]),
        "missing_factual_support": len(report["missing_factual_support"]),
    }


def run_verification_attempt(
    provider_instance: OpenRouterProvider,
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    reasoning_enabled: bool,
    max_output_tokens: int,
    temperature: float,
    response_format: dict[str, Any],
    attempt_number: int,
) -> dict[str, Any]:
    try:
        result = provider_instance.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            reasoning_enabled=reasoning_enabled,
            max_tokens=max_output_tokens,
            temperature=temperature,
            response_format=response_format,
        )
    except ValueError as exc:
        status = classify_provider_exception(str(exc))
        return build_failed_attempt(
            attempt_number,
            status=status,
            provider="openrouter",
            model=model,
            error_message=str(exc),
            api_key=getattr(getattr(provider_instance, "config", None), "api_key", None),
        )

    parsed_payload, parse_errors, parse_status = parse_verification_result(result)
    validation_errors = validate_structured_payload(parsed_payload) if parsed_payload is not None else []
    if parse_status != "ok":
        status = parse_status
    elif validation_errors:
        status = "structured_output_invalid"
    else:
        status = "ok"

    return {
        "attempt": attempt_number,
        "status": status,
        "provider": result.provider,
        "model": result.model,
        "usage": result.usage,
        "reasoning_present": bool(result.reasoning is not None),
        "reasoning_details_present": bool(result.reasoning_details_present),
        "parsed_payload": parsed_payload,
        "parse_errors": parse_errors,
        "validation_errors": validation_errors,
    }


def build_failed_attempt(
    attempt_number: int,
    *,
    status: str,
    provider: str,
    model: str,
    error_message: str,
    api_key: str | None,
) -> dict[str, Any]:
    return {
        "attempt": attempt_number,
        "status": normalize_attempt_status(status),
        "provider": provider,
        "model": model,
        "usage": None,
        "reasoning_present": False,
        "reasoning_details_present": False,
        "parsed_payload": None,
        "parse_errors": [sanitize_string(error_message, api_key=api_key)],
        "validation_errors": [],
    }


def classify_provider_exception(message: str) -> str:
    lower = message.lower()
    if "response_format" in lower or "json_schema" in lower or "schema" in lower:
        return "schema_rejected"
    if (
        "api key" in lower
        or "openrouter_api" in lower
        or "not set" in lower
        or "401" in lower
        or "auth" in lower
        or "unauthorized" in lower
    ):
        return "auth_error"
    if "timeout" in lower or "connection" in lower or "network" in lower:
        return "network_error"
    return "api_error"


def load_generation_metadata(enhanced_path: Path) -> dict[str, Any] | None:
    metadata_path = metadata_output_path(enhanced_path)
    if not metadata_path.exists() or not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid generation metadata JSON: {exc.msg} at line {exc.lineno} column {exc.colno}."
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Generation metadata must contain a JSON object.")
    return payload


def build_verification_context(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    plan_path: Path,
    *,
    generation_metadata: dict[str, Any] | None,
    verification_mode: str,
) -> dict[str, Any]:
    if verification_mode == "same_context" and generation_metadata is not None:
        return build_context_from_generation_metadata(plan, plan_path, generation_metadata)
    return build_context_from_explain_plan(plan, module_target, plan_path)


def build_context_from_generation_metadata(
    plan: dict[str, Any],
    plan_path: Path,
    generation_metadata: dict[str, Any],
) -> dict[str, Any]:
    docs_root = resolve_docs_root(plan, plan_path)
    context_reduction = generation_metadata.get("context_reduction")
    if not isinstance(context_reduction, dict):
        context_reduction = {}

    retained_source = generation_metadata.get("retained_context_files")
    if not isinstance(retained_source, list):
        retained_source = context_reduction.get("retained_context_files", [])
    truncated_source = generation_metadata.get("truncated_context_files")
    if not isinstance(truncated_source, list):
        truncated_source = context_reduction.get("truncated_context_files", [])

    retained_paths = [str(path) for path in retained_source if path]
    truncated_items = [
        item
        for item in truncated_source
        if isinstance(item, dict) and item.get("path")
    ]
    module_doc_path = str(generation_metadata.get("module_doc_path") or "")
    context_paths = unique_strings(
        [
            module_doc_path,
            *retained_paths,
            *[str(item["path"]) for item in truncated_items],
        ]
    )

    documents: list[dict[str, Any]] = []
    for path in context_paths:
        truncated_item = next((item for item in truncated_items if str(item["path"]) == path), None)
        if is_function_index_summary_path(plan, path):
            summary = build_function_index_summary_from_plan(plan)
            if summary:
                documents.append({"path": path, "label": "function_index_summary", "content": summary})
            continue

        document = load_context_document(docs_root, path, label=context_label_for_path(path), required=False)
        if truncated_item is not None:
            document = dict(document)
            document["content"] = reconstruct_truncated_content(document["content"], truncated_item, path)
        documents.append(document)

    context_text = render_context_documents(documents, None)
    return {
        "context_source": "generation_metadata",
        "context_text": context_text,
        "context_files": context_paths,
        "context_fingerprint": str(
            generation_metadata.get("context_fingerprint")
            or context_reduction.get("context_fingerprint")
            or build_context_fingerprint(context_text)
        ),
        "context_reduced": bool(truncated_items),
    }


def build_context_from_explain_plan(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    plan_path: Path,
) -> dict[str, Any]:
    max_input_tokens = int(plan.get("budget_policy", {}).get("max_input_tokens_per_module") or 0)
    context_bundle = build_module_context(
        plan,
        module_target,
        max_input_tokens=max_input_tokens,
        plan_path=plan_path,
    )
    context_reduction = context_bundle.get("context_reduction", {})
    return {
        "context_source": "explain_plan_fallback",
        "context_text": context_bundle["context_text"],
        "context_files": context_bundle["context_files"],
        "context_fingerprint": str(
            context_reduction.get("context_fingerprint")
            or build_context_fingerprint(context_bundle["context_text"])
        ),
        "context_reduced": bool(context_bundle.get("context_reduced")),
    }


def render_verification_prompt(
    module_target: dict[str, Any],
    enhanced_text: str,
    verification_context: dict[str, Any],
    *,
    fallback: bool,
) -> tuple[str, str]:
    system_prompt = (
        prompts.VERIFICATION_SYSTEM_PROMPT.strip()
        + "\n\nПроверяй только по provided factual context. Не исправляй весь markdown. "
        "Не добавляй факты, которых нет в контексте. Верни только JSON object."
    )
    if fallback:
        system_prompt += "\nReasoning отключен для retry. Ответ должен быть только валидным JSON без markdown fences."

    user_prompt = prompts.VERIFICATION_USER_TEMPLATE.format(
        draft_text=enhanced_text,
        module_target_json=json.dumps(module_target, ensure_ascii=False, indent=2),
        context_paths_json=json.dumps(verification_context["context_files"], ensure_ascii=False, indent=2),
    ).strip()
    user_prompt += (
        "\n\nRequired JSON schema contract:\n"
        + json.dumps(verification_json_contract(), ensure_ascii=False, indent=2)
        + "\n\nEvidence refs must be one of these exact context identifiers:\n"
        + json.dumps(verification_context["context_files"], ensure_ascii=False, indent=2)
        + "\n\nRules:\n"
        "- Use empty arrays when no issues are found.\n"
        "- Always include every required field.\n"
        "- Use verdict pass only when no unsupported, weak, or missing support/uncertainty issues are found.\n"
        "- Text values inside JSON must be in Russian.\n"
        "\nFACTUAL CONTEXT BEGIN\n"
        + verification_context["context_text"]
        + "\nFACTUAL CONTEXT END\n\n"
        + "Return only valid JSON."
    )
    if fallback:
        user_prompt += "\nNo prose. No explanations outside JSON. No markdown."
    return system_prompt, user_prompt


def verification_json_contract() -> dict[str, Any]:
    return {
        "unsupported_claims": [
            {
                "claim_text": "string",
                "section": "string",
                "severity": "high|medium|low",
                "reason": "string",
                "evidence_refs": ["context path"],
                "excerpt": "string",
            }
        ],
        "weak_claims": [
            {
                "claim_text": "string",
                "section": "string",
                "reason": "string",
                "suggested_rewrite": "string",
            }
        ],
        "missing_uncertainty": [{"section": "string", "reason": "string"}],
        "missing_factual_support": [{"section": "string", "reason": "string"}],
        "supported_claims_sample": [{"claim_text": "string", "evidence_refs": ["context path"]}],
        "recommended_fixes": [
            {
                "type": "rewrite|downgrade_confidence|add_uncertainty|add_factual_support|remove_claim",
                "target_section": "string",
                "instruction": "string",
            }
        ],
        "verdict": "pass|warning|fail",
    }


def verification_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "docgen_module_verification",
            "strict": True,
            "schema": verification_structured_result_schema(),
        },
    }


def verification_structured_result_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [*VERIFICATION_REQUIRED_LIST_FIELDS, "verdict"],
        "properties": {
            "unsupported_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim_text", "section", "severity", "reason", "evidence_refs", "excerpt"],
                    "properties": {
                        "claim_text": {"type": "string"},
                        "section": {"type": "string"},
                        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "excerpt": {"type": "string"},
                    },
                },
            },
            "weak_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim_text", "section", "reason", "suggested_rewrite"],
                    "properties": {
                        "claim_text": {"type": "string"},
                        "section": {"type": "string"},
                        "reason": {"type": "string"},
                        "suggested_rewrite": {"type": "string"},
                    },
                },
            },
            "missing_uncertainty": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["section", "reason"],
                    "properties": {
                        "section": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "missing_factual_support": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["section", "reason"],
                    "properties": {
                        "section": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "supported_claims_sample": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["claim_text", "evidence_refs"],
                    "properties": {
                        "claim_text": {"type": "string"},
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "recommended_fixes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "target_section", "instruction"],
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "rewrite",
                                "downgrade_confidence",
                                "add_uncertainty",
                                "add_factual_support",
                                "remove_claim",
                            ],
                        },
                        "target_section": {"type": "string"},
                        "instruction": {"type": "string"},
                    },
                },
            },
            "verdict": {"type": "string", "enum": ["pass", "warning", "fail"]},
        },
    }


def verification_report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "generated_at",
            "module",
            "provider",
            "model",
            "verification_mode",
            "context_source",
            "context_fingerprint",
            "enhanced_markdown_path",
            "factual_context_files",
            "usage",
            "reasoning_present",
            "reasoning_details_present",
            "structured_output_valid",
            "parse_errors",
            "provider_status",
            "verifier_status",
            "checks",
            *VERIFICATION_REQUIRED_LIST_FIELDS,
            "verdict",
        ],
        "properties": {
            "schema_version": {"type": "string"},
            "generated_at": {"type": "string"},
            "module": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "verification_mode": {"type": "string", "enum": ["same_context", "fallback_plan"]},
            "context_source": {"type": "string", "enum": ["generation_metadata", "explain_plan_fallback"]},
            "context_fingerprint": {"type": "string"},
            "enhanced_markdown_path": {"type": "string"},
            "factual_context_files": {"type": "array", "items": {"type": "string"}},
            "usage": {"type": ["object", "null"]},
            "usage_first_attempt": {"type": ["object", "null"]},
            "usage_second_attempt": {"type": ["object", "null"]},
            "attempt_usage": {"type": "array"},
            "reasoning_present": {"type": "boolean"},
            "reasoning_details_present": {"type": "boolean"},
            "structured_output_valid": {"type": "boolean"},
            "parse_errors": {"type": "array", "items": {"type": "string"}},
            "provider_status": {
                "type": "object",
                "required": [
                    "attempts",
                    "first_attempt_status",
                    "second_attempt_status",
                    "retry_used",
                    "retry_reason",
                    "final_status",
                ],
            },
            "verifier_status": {"type": "string"},
            "checks": {"type": "object"},
            "unsupported_claims": {"type": "array"},
            "weak_claims": {"type": "array"},
            "missing_uncertainty": {"type": "array"},
            "missing_factual_support": {"type": "array"},
            "supported_claims_sample": {"type": "array"},
            "recommended_fixes": {"type": "array"},
            "verdict": {"type": "string", "enum": ["pass", "warning", "fail"]},
        },
    }


def parse_verification_result(result: Any) -> tuple[dict[str, Any] | None, list[str], str]:
    structured_content = getattr(result, "structured_content", None)
    if structured_content is not None:
        if isinstance(structured_content, dict):
            return dict(structured_content), [], "ok"
        return (
            None,
            ["Verification response exposed structured content, but it was not a JSON object."],
            "provider_response_mismatch",
        )
    return parse_verification_response(getattr(result, "content", ""))


def parse_verification_response(content: str) -> tuple[dict[str, Any] | None, list[str], str]:
    text = (content or "").strip()
    if not text:
        return None, ["Verification response content is empty."], "empty_content"
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None, ["Verification response did not contain a JSON object."], "provider_response_mismatch"
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, [f"Invalid verification JSON: {exc.msg} at line {exc.lineno} column {exc.colno}."], "parse_failure"
    if not isinstance(payload, dict):
        return None, ["Verification response JSON must be an object."], "parse_failure"
    return payload, [], "ok"


def validate_structured_payload(payload: dict[str, Any] | None) -> list[str]:
    if payload is None:
        return ["No structured payload was parsed."]
    errors: list[str] = []
    for field_name in VERIFICATION_REQUIRED_LIST_FIELDS:
        if field_name not in payload:
            errors.append(f"Missing expected field: {field_name}.")
        elif not isinstance(payload[field_name], list):
            errors.append(f"Expected field to be a list: {field_name}.")
        else:
            for index, item in enumerate(payload[field_name]):
                if not isinstance(item, dict):
                    errors.append(f"Expected {field_name}[{index}] to be an object.")
    if normalize_verdict(payload.get("verdict")) is None:
        errors.append("Missing or invalid verdict.")
    for issue in payload.get("unsupported_claims", []) if isinstance(payload.get("unsupported_claims"), list) else []:
        if isinstance(issue, dict) and str(issue.get("severity", "")).lower() not in {"high", "medium", "low"}:
            errors.append("Unsupported claim severity must be high, medium, or low.")
    return errors


def build_verification_report(
    parsed_payload: dict[str, Any] | None,
    *,
    parse_errors: list[str],
    validation_errors: list[str],
    plan: dict[str, Any],
    module_target: dict[str, Any],
    enhanced_path: Path,
    verification_context: dict[str, Any],
    verification_mode: str,
    provider_name: str,
    model: str,
    usage: dict[str, Any] | None,
    usage_first_attempt: dict[str, Any] | None,
    usage_second_attempt: dict[str, Any] | None,
    attempt_usage: list[dict[str, Any]],
    reasoning_present: bool,
    reasoning_details_present: bool,
    provider_status: dict[str, Any],
    api_key: str | None,
    enhanced_text: str,
) -> dict[str, Any]:
    all_errors = [*parse_errors, *validation_errors]
    structured_output_valid = parsed_payload is not None and not all_errors
    verifier_status = "ok" if structured_output_valid else str(provider_status["final_status"])
    allowed_refs = set(verification_context["context_files"])
    payload = parsed_payload or {}
    checks = build_verification_checks(enhanced_text, module_target["name"], api_key=api_key)
    unsupported_claims = normalize_claim_items(
        payload.get("unsupported_claims"), allowed_refs=allowed_refs, api_key=api_key, kind="unsupported"
    )
    weak_claims = normalize_claim_items(
        payload.get("weak_claims"), allowed_refs=allowed_refs, api_key=api_key, kind="weak"
    )
    missing_uncertainty = normalize_claim_items(
        payload.get("missing_uncertainty"), allowed_refs=allowed_refs, api_key=api_key, kind="missing_uncertainty"
    )
    missing_factual_support = normalize_claim_items(
        payload.get("missing_factual_support"), allowed_refs=allowed_refs, api_key=api_key, kind="missing_factual_support"
    )
    supported_claims_sample = normalize_claim_items(
        payload.get("supported_claims_sample"), allowed_refs=allowed_refs, api_key=api_key, kind="supported"
    )
    recommended_fixes = normalize_claim_items(
        payload.get("recommended_fixes"), allowed_refs=allowed_refs, api_key=api_key, kind="fix"
    )

    verdict = normalize_verdict(payload.get("verdict"))
    if not structured_output_valid:
        verdict = "fail"
    elif verdict is None:
        verdict = derive_verdict(unsupported_claims, weak_claims, missing_uncertainty, missing_factual_support)

    report = asdict(
        VerificationReport(
            schema_version="1.0",
            generated_at=timestamp_now(),
            module=str(module_target["name"]),
            provider=provider_name,
            model=model,
            verification_mode=verification_mode,
            context_source=str(verification_context["context_source"]),
            context_fingerprint=str(verification_context["context_fingerprint"]),
            enhanced_markdown_path=enhanced_path.as_posix(),
            factual_context_files=list(verification_context["context_files"]),
            usage=usage,
            usage_first_attempt=usage_first_attempt,
            usage_second_attempt=usage_second_attempt,
            attempt_usage=attempt_usage,
            reasoning_present=reasoning_present,
            reasoning_details_present=reasoning_details_present,
            structured_output_valid=structured_output_valid,
            parse_errors=all_errors,
            provider_status=provider_status,
            verifier_status=verifier_status,
            checks=checks,
            unsupported_claims=unsupported_claims,
            weak_claims=weak_claims,
            missing_uncertainty=missing_uncertainty,
            missing_factual_support=missing_factual_support,
            supported_claims_sample=supported_claims_sample,
            recommended_fixes=recommended_fixes,
            verdict=verdict or "warning",
        )
    )
    report = sanitize_payload(report, api_key=api_key)
    report_validation_errors = validate_verification_report(report)
    if report_validation_errors:
        report["structured_output_valid"] = False
        report["verifier_status"] = "structured_output_invalid"
        report["parse_errors"] = unique_strings([*report["parse_errors"], *report_validation_errors])
        report["verdict"] = "fail"
    return report


def build_provider_status(
    attempts: list[dict[str, Any]],
    *,
    retry_used: bool,
    retry_reason: str | None,
) -> dict[str, Any]:
    first_status = normalize_attempt_status(attempts[0]["status"] if attempts else "skipped")
    second_status = normalize_attempt_status(attempts[1]["status"]) if len(attempts) > 1 else None
    final_status = normalize_attempt_status(attempts[-1]["status"] if attempts else "skipped")
    return asdict(
        ProviderStatus(
            attempts=len(attempts),
            first_attempt_status=first_status,
            second_attempt_status=second_status,
            retry_used=retry_used,
            retry_reason=retry_reason,
            final_status=final_status,
        )
    )


def build_verification_checks(markdown_text: str, module_name: str, *, api_key: str | None) -> dict[str, Any]:
    validation = validate_enhanced_markdown(markdown_text, module_name=module_name, api_key=api_key)
    return asdict(
        VerificationChecks(
            required_sections_present=bool(validation.get("required_sections_present")),
            factual_support_section_present="## Фактическая опора" in markdown_text,
            uncertainty_section_present="## Что не удалось определить" in markdown_text,
            contains_reasoning_details="reasoning_details" in markdown_text,
            contains_api_key_leak=bool(
                "OPENROUTER_API=" in markdown_text or (api_key and api_key in markdown_text)
            ),
        )
    )


def validate_verification_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_fields = verification_report_schema()["required"]
    for field_name in required_fields:
        if field_name not in report:
            errors.append(f"Verification report missing field: {field_name}.")
    provider_status = report.get("provider_status")
    if not isinstance(provider_status, dict):
        errors.append("Verification report provider_status must be an object.")
    else:
        attempts = provider_status.get("attempts")
        if attempts not in {1, 2}:
            errors.append("provider_status.attempts must be 1 or 2.")
        for key in ("first_attempt_status", "final_status"):
            if normalize_attempt_status(provider_status.get(key)) != provider_status.get(key):
                errors.append(f"provider_status.{key} has an unsupported status.")
        second_status = provider_status.get("second_attempt_status")
        if second_status is not None and normalize_attempt_status(second_status) != second_status:
            errors.append("provider_status.second_attempt_status has an unsupported status.")
    if report.get("verdict") not in VERIFICATION_VERDICTS:
        errors.append("Verification report verdict is missing or invalid.")
    for field_name in VERIFICATION_REQUIRED_LIST_FIELDS:
        if not isinstance(report.get(field_name), list):
            errors.append(f"Verification report field must be a list: {field_name}.")
    if report.get("structured_output_valid") is True and report.get("verifier_status") != "ok":
        errors.append("verifier_status must be ok when structured_output_valid is true.")
    return errors


def normalize_claim_items(
    value: Any,
    *,
    allowed_refs: set[str],
    api_key: str | None,
    kind: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        payload = {
            sanitize_string(str(key), api_key=api_key): sanitize_string(str(val), api_key=api_key)
            for key, val in item.items()
            if not isinstance(val, list)
        }
        if "evidence_refs" in item:
            payload["evidence_refs"] = filter_evidence_refs(item.get("evidence_refs"), allowed_refs)
        elif kind in {"unsupported", "supported"}:
            payload["evidence_refs"] = []
        normalized.append(payload)
    return normalized


def filter_evidence_refs(value: Any, allowed_refs: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        ref = str(item).replace("\\", "/")
        if ref in allowed_refs:
            refs.append(ref)
            continue
        if ref.startswith("docs/generated/"):
            stripped = ref[len("docs/generated/") :]
            if stripped in allowed_refs:
                refs.append(stripped)
    return unique_strings(refs)


def normalize_verdict(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in VERIFICATION_VERDICTS else None


def normalize_attempt_status(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in ATTEMPT_STATUSES else "api_error"


def derive_verdict(
    unsupported_claims: list[dict[str, Any]],
    weak_claims: list[dict[str, Any]],
    missing_uncertainty: list[dict[str, Any]],
    missing_factual_support: list[dict[str, Any]],
) -> str:
    if any(str(item.get("severity", "")).lower() == "high" for item in unsupported_claims):
        return "fail"
    if unsupported_claims or weak_claims or missing_uncertainty or missing_factual_support:
        return "warning"
    return "pass"


def render_verification_summary(report: dict[str, Any]) -> str:
    extraction_failed = report.get("verifier_status") != "ok"
    failure_note = (
        "Проверка не дала валидного structured result. Списки ниже могут быть пустыми из-за failure extraction, "
        "а не потому что проблем нет."
        if extraction_failed
        else ""
    )
    sections = [
        f"# Проверка модуля: {report.get('module')}",
        "",
        "## Вердикт",
        str(report.get("verdict") or "нет данных"),
        "",
        "## Статус проверки",
        f"- verifier_status: {report.get('verifier_status')}",
        f"- provider_status: {json.dumps(report.get('provider_status'), ensure_ascii=False)}",
        f"- structured_output_valid: {report.get('structured_output_valid')}",
        failure_note,
        "",
        "## Unsupported claims",
        render_summary_items(report.get("unsupported_claims"), "claim_text", extraction_failed=extraction_failed),
        "",
        "## Weak claims",
        render_summary_items(report.get("weak_claims"), "claim_text", extraction_failed=extraction_failed),
        "",
        "## Missing uncertainty",
        render_summary_items(report.get("missing_uncertainty"), "section", extraction_failed=extraction_failed),
        "",
        "## Missing factual support",
        render_summary_items(report.get("missing_factual_support"), "section", extraction_failed=extraction_failed),
        "",
        "## Recommended fixes",
        render_summary_items(report.get("recommended_fixes"), "instruction", extraction_failed=extraction_failed),
        "",
        "## Контекст проверки",
        f"- context_source: {report.get('context_source')}",
        f"- context_fingerprint: {report.get('context_fingerprint')}",
        *[f"- {path}" for path in report.get("factual_context_files", [])],
        "",
        "## Ошибки извлечения",
        render_parse_errors(report.get("parse_errors")),
        "",
    ]
    return "\n".join(section for section in sections if section is not None)


def render_summary_items(value: Any, primary_key: str, *, extraction_failed: bool) -> str:
    if not isinstance(value, list) or not value:
        if extraction_failed:
            return "нет данных: structured verification не состоялась, поэтому отсутствие записей не означает отсутствие проблем"
        return "нет данных"
    lines: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        primary = item.get(primary_key) or item.get("reason") or item.get("claim_text") or item.get("section")
        if primary:
            lines.append(f"- {primary}")
    return "\n".join(lines) if lines else "нет данных"


def render_parse_errors(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "нет данных"
    return "\n".join(f"- {item}" for item in value)


def verification_summary_path(output_path: Path) -> Path:
    return output_path.with_suffix(".md")


def retry_policy(max_output_tokens: int) -> dict[str, Any]:
    return {
        "max_attempts": 2,
        "retry_statuses": sorted(RETRYABLE_ATTEMPT_STATUSES),
        "first_attempt_response_format": "json_schema",
        "fallback_response_format": "json_object",
        "fallback_reasoning_enabled": False,
        "fallback_max_output_tokens": min(max_output_tokens, FALLBACK_VERIFICATION_MAX_OUTPUT_TOKENS),
    }


def merge_usage(usages: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    totals: dict[str, int] = {}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            try:
                totals[key] = totals.get(key, 0) + int(value)
            except (TypeError, ValueError):
                continue
    return totals or None


def reconstruct_truncated_content(content: str, truncated_item: dict[str, Any], path: str) -> str:
    original_chars = parse_int_value(truncated_item.get("original_chars"))
    retained_chars = parse_int_value(truncated_item.get("retained_chars"))
    if original_chars is not None and retained_chars is not None and original_chars <= retained_chars:
        return content
    limit = summary_limit_for_path(path)
    candidate = truncate_context_content(content, limit) if limit is not None else content
    if retained_chars is not None and len(candidate) != retained_chars:
        return candidate[:retained_chars]
    return candidate


def summary_limit_for_path(path: str) -> int | None:
    normalized = path.replace("\\", "/")
    if normalized == "dependency-map.md":
        return 6_000
    if normalized == "module-map.md":
        return 4_000
    if normalized == "files/index.md":
        return 2_500
    if normalized.startswith("files/file-"):
        return 2_500
    return None


def context_label_for_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized == "coverage-report.md":
        return "coverage_report"
    if normalized == "dependency-map.md":
        return "dependency_map"
    if normalized == "module-map.md":
        return "module_map"
    if normalized == "files/index.md":
        return "file_index"
    if normalized.startswith("modules/"):
        return "module_doc"
    if normalized.startswith("files/file-"):
        return f"file_doc:{normalized}"
    return normalized


def is_function_index_summary_path(plan: dict[str, Any], path: str) -> bool:
    global_docs = plan.get("global_docs", {})
    if not isinstance(global_docs, dict):
        return False
    function_index = global_docs.get("function_index")
    return isinstance(function_index, dict) and str(function_index.get("path") or "") == path


def build_function_index_summary_from_plan(plan: dict[str, Any]) -> str | None:
    global_docs = plan.get("global_docs", {}) if isinstance(plan.get("global_docs"), dict) else {}
    context_policy = plan.get("context_policy", {}) if isinstance(plan.get("context_policy"), dict) else {}
    return build_function_index_summary(global_docs, context_policy)


def parse_int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sanitize_payload(value: Any, *, api_key: str | None) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_payload(item, api_key=api_key) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item, api_key=api_key) for item in value]
    if isinstance(value, str):
        return sanitize_string(value, api_key=api_key)
    return value


def sanitize_string(value: str, *, api_key: str | None) -> str:
    text = value.replace("reasoning_details", "[redacted_reasoning_field]")
    text = text.replace("OPENROUTER_API=", "OPENROUTER_API_REDACTED=")
    if api_key:
        text = text.replace(api_key, "[redacted_api_key]")
    return text


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()
