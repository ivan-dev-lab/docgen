from __future__ import annotations

import json
import re
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, prompts
from .openrouter_provider import OpenRouterProvider

REQUIRED_MARKDOWN_SECTIONS = [
    "## Что известно",
    "## Назначение",
    "## Как работает",
    "## Контур взаимодействия",
    "## Ключевые функции",
    "## Зависимости",
    "## Что не удалось определить",
    "## Уровень уверенности",
    "## Фактическая опора",
]

STRUCTURED_ANALYSIS_SECTIONS = [
    "## Что известно",
    "## Назначение",
    "## Как работает",
    "## Контур взаимодействия",
    "## Ключевые функции",
    "## Зависимости",
]

STRUCTURE_MARKERS = ("Факты", "Интерпретации", "Неизвестно")
UNCERTAINTY_PHRASES = (
    "не удалось определить",
    "по предоставленным фактам",
    "можно предположить",
    "требует ручной проверки",
    "вероятно",
    "возможно",
    "похоже",
)
OVERCONFIDENT_PATTERNS = (
    "предназначен",
    "отвечает за",
    "использует внешний api",
    "выполняет",
    "гарантирует",
)
RAW_RESPONSE_MARKERS = ('"choices"', '"usage"', '"prompt_tokens"', '"completion_tokens"', '"total_tokens"')
NO_DATA_SENTENCE = "Не удалось определить по предоставленным фактам."
PROMPT_SAFETY_MARGIN_RATIO = 0.35
PROMPT_SAFETY_MARGIN_MIN_TOKENS = 512
DOCUMENT_SUMMARY_LIMITS = {
    "dependency_map": 6_000,
    "module_map": 4_000,
    "file_index": 2_500,
    "file_doc": 2_500,
}
CONTEXT_REDUCTION_STRATEGY = "stage_3c_module_first_keep_module_coverage_dependency_then_file_docs_in_plan_order"


@dataclass(frozen=True, slots=True)
class TruncatedContextFile:
    path: str
    truncation_mode: str
    original_chars: int
    retained_chars: int
    original_estimated_tokens: int
    retained_estimated_tokens: int


@dataclass(frozen=True, slots=True)
class ReductionPlan:
    pre_reduction_estimated_context_tokens: int
    pre_reduction_estimated_prompt_tokens: int
    pre_reduction_estimated_prompt_tokens_with_margin: int
    post_reduction_estimated_context_tokens: int
    post_reduction_estimated_prompt_tokens: int
    post_reduction_estimated_prompt_tokens_with_margin: int
    max_input_tokens: int
    reduction_was_required: bool
    reduction_applied: bool
    reduction_reason: str | None
    retained_context_files: list[str]
    dropped_context_files: list[str]
    truncated_context_files: list[TruncatedContextFile]
    notes: list[str]
    context_fingerprint: str


def explain_module(
    plan_path: Path,
    module_name: str,
    output_path: Path,
    *,
    provider_name: str = "openrouter",
    model: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    force_skip: bool = False,
    allow_over_budget: bool = False,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    temperature: float = 0.2,
    reasoning: bool | None = None,
    save_raw: bool = False,
    provider: OpenRouterProvider | None = None,
) -> dict[str, Any]:
    if save_raw:
        raise ValueError("save-raw is not implemented in Stage 3C.")

    plan = load_explain_plan(plan_path)
    module_target = select_module_target(plan, module_name)
    resolved_provider = provider_name or str(plan.get("model_plan", {}).get("provider") or config.DEFAULT_PROVIDER)
    if resolved_provider != "openrouter":
        raise ValueError(f"Unsupported provider: {resolved_provider}")

    if output_path.exists() and not force and not dry_run:
        raise ValueError(f"Output file already exists: {output_path}. Use --force to overwrite it.")

    resolved_max_input_tokens = int(max_input_tokens or plan.get("budget_policy", {}).get("max_input_tokens_per_module") or 0)
    resolved_max_output_tokens = int(
        max_output_tokens or plan.get("budget_policy", {}).get("max_output_tokens_per_module") or 0
    )
    reasoning_enabled = (
        reasoning
        if reasoning is not None
        else bool(plan.get("model_plan", {}).get("reasoning_enabled", config.DEFAULT_REASONING_ENABLED))
    )
    resolved_model = model or str(plan.get("model_plan", {}).get("default_model") or config.DEFAULT_MODEL)

    context_bundle = build_module_context(
        plan,
        module_target,
        max_input_tokens=resolved_max_input_tokens,
        plan_path=plan_path,
    )

    summary = {
        "module": module_target["name"],
        "type": module_target.get("type"),
        "module_page_role": module_target.get("module_page_role"),
        "explain_mode": module_target.get("explain_mode"),
        "priority": module_target.get("priority"),
        "provider": resolved_provider,
        "model": resolved_model,
        "estimated_input_tokens": context_bundle["token_budget"]["estimated_prompt_tokens"],
        "max_input_tokens": resolved_max_input_tokens,
        "estimated_output_tokens": resolved_max_output_tokens,
        "context_files": context_bundle["context_files"],
        "retained_context_files": context_bundle["retained_context_files"],
        "dropped_context_files": context_bundle["dropped_context_files"],
        "truncated_context_files": context_bundle["truncated_context_files"],
        "context_reduced": context_bundle["context_reduced"],
        "context_reduction_reason": context_bundle["context_reduction_reason"],
        "context_reduction": context_bundle["context_reduction"],
        "context_warnings": context_bundle["context_warnings"],
        "planned_output": output_path.as_posix(),
        "token_budget": context_bundle["token_budget"],
        "reduction_was_required": context_bundle["reduction_plan"].reduction_was_required,
        "reduction_applied": context_bundle["reduction_plan"].reduction_applied,
        "reduction_reason": context_bundle["reduction_plan"].reduction_reason,
        "pre_reduction_estimated_prompt_tokens_with_margin": (
            context_bundle["reduction_plan"].pre_reduction_estimated_prompt_tokens_with_margin
        ),
        "post_reduction_estimated_prompt_tokens_with_margin": (
            context_bundle["reduction_plan"].post_reduction_estimated_prompt_tokens_with_margin
        ),
        "dropped_context_files_count": len(context_bundle["reduction_plan"].dropped_context_files),
        "truncated_context_files_count": len(context_bundle["reduction_plan"].truncated_context_files),
        "context_fingerprint": context_bundle["reduction_plan"].context_fingerprint,
        "network_call": False,
    }

    if module_target.get("explain_mode") == "skip" and not force_skip:
        warning = (
            f"Module '{module_target['name']}' has explain_mode=skip. "
            "Use --force-skip to override live generation."
        )
        if dry_run:
            summary["skipped"] = True
            summary["skip_reason"] = warning
            return summary
        raise ValueError(warning)

    prompt_over_budget = (
        resolved_max_input_tokens > 0
        and context_bundle["reduction_plan"].post_reduction_estimated_prompt_tokens_with_margin
        > resolved_max_input_tokens
    )
    if dry_run:
        if prompt_over_budget:
            summary["would_require_allow_over_budget"] = True
        return summary

    if prompt_over_budget and not allow_over_budget:
        raise ValueError(
            "Estimated prompt tokens still exceed max_input_tokens after context reduction "
            f"({context_bundle['reduction_plan'].post_reduction_estimated_prompt_tokens_with_margin} > "
            f"{resolved_max_input_tokens}). Use --allow-over-budget to continue."
        )

    provider_instance = provider or OpenRouterProvider(
        config.build_openrouter_config(
            model=resolved_model,
            reasoning_enabled=reasoning_enabled,
        )
    )
    result = provider_instance.complete(
        [
            {"role": "system", "content": context_bundle["system_prompt"]},
            {"role": "user", "content": context_bundle["user_prompt"]},
        ],
        model=resolved_model,
        reasoning_enabled=reasoning_enabled,
        max_tokens=resolved_max_output_tokens,
        temperature=temperature,
    )

    provider_api_key = getattr(getattr(provider_instance, "config", None), "api_key", None)
    markdown_text = normalize_generated_markdown(result.content, module_target["name"])
    markdown_text = sanitize_generated_markdown(markdown_text, api_key=provider_api_key)
    markdown_text = normalize_entity_count_wording(markdown_text, module_target)
    validation = validate_enhanced_markdown(
        markdown_text,
        module_name=module_target["name"],
        api_key=provider_api_key,
    )
    semantic_validation = validate_semantic_markdown(
        markdown_text,
        entity_count=parse_optional_int(module_target.get("entity_count")),
    )
    markdown_text = ensure_trailing_newline(markdown_text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_text, encoding="utf-8", newline="\n")

    token_budget = build_token_budget(
        context_bundle=context_bundle,
        max_input_tokens=resolved_max_input_tokens,
        usage=result.usage,
        override_used=bool(prompt_over_budget and allow_over_budget),
    )
    metadata_path = metadata_output_path(output_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "generated_at": timestamp_now(),
        "provider": result.provider,
        "model": result.model,
        "module": module_target["name"],
        "module_doc_path": module_target.get("module_doc_path"),
        "output_path": output_path.as_posix(),
        "explain_mode": module_target.get("explain_mode"),
        "priority": module_target.get("priority"),
        "estimated_input_tokens": token_budget["estimated_prompt_tokens"],
        "max_input_tokens": resolved_max_input_tokens,
        "max_output_tokens": resolved_max_output_tokens,
        "context_files": context_bundle["context_files"],
        "retained_context_files": context_bundle["retained_context_files"],
        "dropped_context_files": context_bundle["dropped_context_files"],
        "truncated_context_files": context_bundle["truncated_context_files"],
        "context_reduced": context_bundle["context_reduced"],
        "context_reduction_reason": context_bundle["context_reduction_reason"],
        "context_reduction": context_bundle["context_reduction"],
        "context_warnings": context_bundle["context_warnings"],
        "token_budget": token_budget,
        "usage": result.usage,
        "reasoning_present": bool(result.reasoning is not None),
        "reasoning_details_present": bool(result.reasoning_details_present),
        "markdown_validation": validation,
        "semantic_validation": semantic_validation,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        **summary,
        "network_call": True,
        "output_path": output_path.as_posix(),
        "metadata_path": metadata_path.as_posix(),
        "usage": result.usage,
        "reasoning_present": bool(result.reasoning is not None),
        "reasoning_details_present": bool(result.reasoning_details_present),
        "token_budget": token_budget,
        "context_reduction": context_bundle["context_reduction"],
        "markdown_validation": validation,
        "semantic_validation": semantic_validation,
    }


def load_explain_plan(plan_path: Path) -> dict[str, Any]:
    if not plan_path.exists():
        raise FileNotFoundError(f"Explain plan does not exist: {plan_path}")
    if not plan_path.is_file():
        raise ValueError(f"Explain plan path is not a file: {plan_path}")
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in explain plan: {exc.msg} at line {exc.lineno} column {exc.colno}."
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Explain plan must contain a JSON object at the top level.")
    if not isinstance(payload.get("modules"), list):
        raise ValueError("Explain plan is missing the modules list.")
    return payload


def select_module_target(plan: dict[str, Any], module_name: str) -> dict[str, Any]:
    matches = [
        module
        for module in plan.get("modules", [])
        if isinstance(module, dict) and str(module.get("name") or "") == module_name
    ]
    if not matches:
        available = "\n".join(
            f"- {module.get('name')} ({module.get('type')}, {module.get('module_page_role')}, "
            f"explain_mode={module.get('explain_mode')}, priority={module.get('priority')})"
            for module in sorted(
                [module for module in plan.get("modules", []) if isinstance(module, dict)],
                key=lambda item: (
                    str(item.get("name") or ""),
                    str(item.get("type") or ""),
                    str(item.get("module_page_role") or ""),
                ),
            )
        )
        raise ValueError(f"Module '{module_name}' was not found in explain-plan.\nAvailable modules:\n{available}")
    if len(matches) > 1:
        details = "\n".join(
            f"- {module.get('name')} ({module.get('type')}, {module.get('module_doc_path')})"
            for module in matches
        )
        raise ValueError(f"Ambiguous module '{module_name}'. Matches:\n{details}")
    return dict(matches[0])


def build_module_context(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    max_input_tokens: int,
    *,
    plan_path: Path,
) -> dict[str, Any]:
    docs_root = resolve_docs_root(plan, plan_path)
    global_docs = plan.get("global_docs", {}) if isinstance(plan.get("global_docs"), dict) else {}
    context_policy = plan.get("context_policy", {}) if isinstance(plan.get("context_policy"), dict) else {}
    warnings: list[str] = []

    documents = collect_context_documents(docs_root, global_docs, module_target)
    function_index_summary = build_function_index_summary(global_docs, context_policy)
    pre_bundle = finalize_context_bundle(
        plan,
        module_target,
        docs_root,
        documents,
        function_index_summary,
        max_input_tokens,
    )
    reduction_plan, reduced_documents = build_reduction_plan(
        plan,
        module_target,
        docs_root,
        documents,
        function_index_summary,
        max_input_tokens,
        pre_bundle=pre_bundle,
    )
    bundle = finalize_context_bundle(
        plan,
        module_target,
        docs_root,
        reduced_documents,
        function_index_summary,
        max_input_tokens,
    )

    if reduction_plan.reduction_was_required:
        warnings.append("estimated_prompt_tokens_over_budget")
    if reduction_plan.reduction_applied:
        warnings.append("context_reduced_for_prompt_budget")
    if (
        max_input_tokens > 0
        and reduction_plan.post_reduction_estimated_prompt_tokens_with_margin > max_input_tokens
    ):
        warnings.append("estimated_prompt_tokens_still_over_budget_after_reduction")
    if reduction_plan.reduction_was_required and not reduction_plan.reduction_applied:
        warnings.append("context_reduction_required_but_no_context_changed")

    bundle["reduction_plan"] = reduction_plan
    bundle["context_reduction"] = build_context_reduction_metadata(reduction_plan)
    bundle["context_reduced"] = reduction_plan.reduction_applied
    bundle["context_reduction_reason"] = reduction_plan.reduction_reason
    bundle["dropped_context_files"] = list(reduction_plan.dropped_context_files)
    bundle["truncated_context_files"] = [asdict(item) for item in reduction_plan.truncated_context_files]
    bundle["retained_context_files"] = list(bundle["context_files"])
    bundle["context_warnings"] = sorted(unique_strings([*bundle["context_warnings"], *warnings]))
    bundle["token_budget"] = build_token_budget(
        context_bundle=bundle,
        max_input_tokens=max_input_tokens,
        usage=None,
        override_used=False,
    )
    return bundle


def build_reduction_plan(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    docs_root: Path,
    documents: list[dict[str, Any]],
    function_index_summary: str | None,
    max_input_tokens: int,
    *,
    pre_bundle: dict[str, Any],
) -> tuple[ReductionPlan, list[dict[str, Any]]]:
    pre_tokens = pre_bundle["token_budget"]
    reduction_was_required = (
        max_input_tokens > 0
        and pre_tokens["estimated_prompt_tokens_with_margin"] > max_input_tokens
    )
    notes: list[str] = []

    if reduction_was_required:
        notes.append(
            "pre_reduction_estimated_prompt_tokens_with_margin exceeded max_input_tokens"
        )
        reduced_documents, dropped_files = reduce_context_documents(
            plan,
            module_target,
            documents,
            function_index_summary,
            max_input_tokens,
            docs_root=docs_root,
        )
        reduction_reason = "estimated_prompt_tokens_over_budget"
    else:
        reduced_documents = [dict(document) for document in documents]
        dropped_files = []
        reduction_reason = None

    post_bundle = finalize_context_bundle(
        plan,
        module_target,
        docs_root,
        reduced_documents,
        function_index_summary,
        max_input_tokens,
    )
    post_tokens = post_bundle["token_budget"]
    truncated_context_files = build_truncated_context_files(reduced_documents)
    truncated_paths = {item.path for item in truncated_context_files}
    dropped_context_files = [
        path for path in unique_strings(dropped_files) if path not in truncated_paths
    ]
    retained_context_files = [
        path
        for path in post_bundle["context_files"]
        if path not in truncated_paths and path not in dropped_context_files
    ]

    if reduction_was_required and dropped_context_files:
        notes.append("optional context files were dropped to stay within the input budget")
    if reduction_was_required and truncated_context_files:
        notes.append("some retained context files were truncated to deterministic char limits")
    if (
        reduction_was_required
        and not dropped_context_files
        and not truncated_context_files
    ):
        notes.append("no eligible context files could be dropped or truncated")

    original_context = pre_bundle["context_text"]
    reduced_context = post_bundle["context_text"]
    reduction_applied = reduction_was_required and (
        bool(dropped_context_files)
        or bool(truncated_context_files)
        or original_context != reduced_context
    )
    if not reduction_applied:
        dropped_context_files = []
        truncated_context_files = []
        retained_context_files = list(post_bundle["context_files"])

    return (
        ReductionPlan(
            pre_reduction_estimated_context_tokens=pre_tokens["estimated_context_tokens"],
            pre_reduction_estimated_prompt_tokens=pre_tokens["estimated_prompt_tokens"],
            pre_reduction_estimated_prompt_tokens_with_margin=pre_tokens[
                "estimated_prompt_tokens_with_margin"
            ],
            post_reduction_estimated_context_tokens=post_tokens["estimated_context_tokens"],
            post_reduction_estimated_prompt_tokens=post_tokens["estimated_prompt_tokens"],
            post_reduction_estimated_prompt_tokens_with_margin=post_tokens[
                "estimated_prompt_tokens_with_margin"
            ],
            max_input_tokens=max_input_tokens,
            reduction_was_required=reduction_was_required,
            reduction_applied=reduction_applied,
            reduction_reason=reduction_reason,
            retained_context_files=unique_strings(retained_context_files),
            dropped_context_files=unique_strings(dropped_context_files),
            truncated_context_files=truncated_context_files,
            notes=unique_strings(notes),
            context_fingerprint=build_context_fingerprint(post_bundle["context_text"]),
        ),
        reduced_documents,
    )


def build_truncated_context_files(documents: list[dict[str, Any]]) -> list[TruncatedContextFile]:
    truncated: list[TruncatedContextFile] = []
    for document in documents:
        truncation = document.get("truncation")
        if not isinstance(truncation, dict):
            continue
        truncated.append(
            TruncatedContextFile(
                path=str(truncation["path"]),
                truncation_mode=str(truncation["truncation_mode"]),
                original_chars=int(truncation["original_chars"]),
                retained_chars=int(truncation["retained_chars"]),
                original_estimated_tokens=int(truncation["original_estimated_tokens"]),
                retained_estimated_tokens=int(truncation["retained_estimated_tokens"]),
            )
        )
    return truncated


def build_context_reduction_metadata(reduction_plan: ReductionPlan) -> dict[str, Any]:
    return {
        "strategy": CONTEXT_REDUCTION_STRATEGY,
        "retained_context_files": list(reduction_plan.retained_context_files),
        "dropped_context_files": list(reduction_plan.dropped_context_files),
        "truncated_context_files": [
            asdict(item) for item in reduction_plan.truncated_context_files
        ],
        "notes": list(reduction_plan.notes),
        "context_fingerprint": reduction_plan.context_fingerprint,
    }


def build_context_fingerprint(context_text: str) -> str:
    return hashlib.sha256(context_text.encode("utf-8")).hexdigest()


def collect_context_documents(
    docs_root: Path,
    global_docs: dict[str, Any],
    module_target: dict[str, Any],
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []

    if module_target.get("module_doc_exists") and module_target.get("module_doc_path"):
        documents.append(
            enrich_document(
                load_context_document(
                    docs_root,
                    str(module_target["module_doc_path"]),
                    label="module_doc",
                    required=True,
                ),
                priority=0,
                score=0,
            )
        )

    for global_key, priority in (("coverage_report", 1), ("dependency_map", 2), ("module_map", 3), ("file_index", 5)):
        ref = global_docs.get(global_key)
        if isinstance(ref, dict) and ref.get("exists") and ref.get("path"):
            documents.append(
                enrich_document(
                    load_context_document(
                        docs_root,
                        str(ref["path"]),
                        label=global_key,
                        required=global_key in {"coverage_report", "dependency_map"},
                    ),
                    priority=priority,
                    score=0,
                )
            )

    file_index_counts = parse_file_index_counts(
        next((document["content"] for document in documents if document["label"] == "file_index"), "")
    )
    file_documents: list[dict[str, Any]] = []
    for file_doc in module_target.get("file_doc_paths", []):
        if not isinstance(file_doc, dict):
            continue
        if not file_doc.get("exists") or not file_doc.get("doc_path"):
            continue
        source_file = str(file_doc.get("source_file") or "")
        counts = file_index_counts.get(source_file, {})
        entity_count = int(counts.get("entity_count") or 0)
        import_count = int(counts.get("import_count") or 0)
        score = (entity_count * 1000) + import_count
        document = enrich_document(
            load_context_document(
                docs_root,
                str(file_doc["doc_path"]),
                label=f"file_doc:{source_file}",
                required=False,
            ),
            priority=4,
            score=score,
        )
        document["source_file"] = source_file
        document["entity_count"] = entity_count
        document["import_count"] = import_count
        file_documents.append(document)

    documents.extend(file_documents)
    return documents


def finalize_context_bundle(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    docs_root: Path,
    documents: list[dict[str, Any]],
    function_index_summary: str | None,
    max_input_tokens: int,
) -> dict[str, Any]:
    context_text = render_context_documents(documents, function_index_summary)
    context_files = [document["path"] for document in documents]
    if function_index_summary:
        function_index_path = resolve_global_doc_path(plan, "function_index")
        if function_index_path:
            context_files.append(function_index_path)
    context_files = unique_strings(context_files)

    prompt_stub = {
        "context_text": context_text,
        "context_files": context_files,
    }
    system_prompt, user_prompt = render_module_prompt(plan, module_target, prompt_stub)
    estimated_context_tokens = estimate_tokens(context_text)
    estimated_prompt_tokens = estimate_tokens(system_prompt + "\n" + user_prompt)
    prompt_safety_margin_tokens = calculate_prompt_safety_margin(estimated_prompt_tokens)
    estimated_prompt_tokens_with_margin = estimated_prompt_tokens + prompt_safety_margin_tokens

    return {
        "docs_root": docs_root,
        "documents": documents,
        "context_text": context_text,
        "context_files": context_files,
        "retained_context_files": list(context_files),
        "dropped_context_files": [],
        "estimated_input_tokens": estimated_prompt_tokens,
        "estimated_context_tokens": estimated_context_tokens,
        "context_reduced": any(document.get("reduced") for document in documents),
        "context_reduction_reason": None,
        "context_warnings": [],
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "token_budget": {
            "estimation_method": "approx_chars_div_4",
            "estimated_context_tokens": estimated_context_tokens,
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "prompt_safety_margin_tokens": prompt_safety_margin_tokens,
            "estimated_prompt_tokens_with_margin": estimated_prompt_tokens_with_margin,
            "actual_prompt_tokens": None,
            "actual_completion_tokens": None,
            "actual_total_tokens": None,
            "max_input_tokens": max_input_tokens,
            "prompt_budget_exceeded": estimated_prompt_tokens_with_margin > max_input_tokens if max_input_tokens > 0 else False,
            "prompt_budget_delta": (
                estimated_prompt_tokens_with_margin - max_input_tokens if max_input_tokens > 0 else 0
            ),
        },
    }


def build_function_index_summary(global_docs: dict[str, Any], context_policy: dict[str, Any]) -> str | None:
    function_index_ref = global_docs.get("function_index")
    if (
        isinstance(function_index_ref, dict)
        and function_index_ref.get("exists")
        and function_index_ref.get("path")
        and context_policy.get("include_function_index") == "summary_or_links_only"
    ):
        return (
            "Function index available as navigation-only context: "
            f"{function_index_ref['path']}. Use it only as a summary/link source."
        )
    return None


def render_module_prompt(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    context_bundle: dict[str, Any],
) -> tuple[str, str]:
    output_contract = plan.get("output_contract", {})
    verification_policy = plan.get("verification_policy", {})
    global_docs = plan.get("global_docs", {})

    system_prompt = (
        prompts.MODULE_EXPLANATION_SYSTEM_PROMPT.strip()
        + "\n\n"
        + "Пиши только на русском языке.\n"
        + "Используй точные заголовки разделов из output contract.\n"
        + "В каждом смысловом разделе явно отделяй блоки: `Факты`, `Интерпретации`, `Неизвестно`.\n"
        + "Формулировки без прямой опоры на факты помечай как интерпретации или неизвестное.\n"
        + "Если данных недостаточно, пиши: "
        + f"\"{NO_DATA_SENTENCE}\""
    )
    user_prompt = prompts.MODULE_EXPLANATION_USER_TEMPLATE.format(
        module_target_json=json.dumps(module_target, ensure_ascii=False, indent=2),
        global_doc_refs_json=json.dumps(global_docs, ensure_ascii=False, indent=2),
        context_paths_json=json.dumps(context_bundle["context_files"], ensure_ascii=False, indent=2),
    ).strip()
    user_prompt += (
        "\n\n"
        "Нужно вернуть markdown на русском языке.\n"
        "Обязательная структура ответа:\n"
        f"# Модуль: {module_target['name']}\n"
        + "\n".join(REQUIRED_MARKDOWN_SECTIONS)
        + "\n\n"
        "Для разделов `Что известно`, `Назначение`, `Как работает`, `Контур взаимодействия`, "
        "`Ключевые функции`, `Зависимости` обязательно используй подпункты или маркеры с названиями:\n"
        "- Факты\n"
        "- Интерпретации\n"
        "- Неизвестно\n\n"
        "Запрещено писать категорично `модуль предназначен`, `модуль отвечает за`, `система использует`, "
        "если это не подтверждено фактами в контексте. В таких случаях используй осторожные формулировки: "
        "`по предоставленным фактам видно`, `можно предположить`, `точное бизнес-назначение не определено`.\n\n"
        "Если module target содержит `entity_count`, считай статистическое количество сущностей известным. "
        "Не пиши, что точное количество всех существующих сущностей неизвестно; вместо этого отделяй известную статистику от отсутствия полного детального списка из-за усечения контекста.\n\n"
        "Output contract:\n"
        + json.dumps(output_contract, ensure_ascii=False, indent=2)
        + "\n\nVerification policy:\n"
        + json.dumps(verification_policy, ensure_ascii=False, indent=2)
        + "\n\nPrompt output contract reference:\n"
        + prompts.MODULE_EXPLANATION_OUTPUT_CONTRACT.strip()
        + "\n\nFACTUAL CONTEXT BEGIN\n"
        + context_bundle["context_text"]
        + "\nFACTUAL CONTEXT END\n"
    )
    return system_prompt, user_prompt


def validate_enhanced_markdown(
    markdown_text: str,
    *,
    module_name: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    text = markdown_text or ""
    lower_text = text.lower()
    missing_sections = [section for section in REQUIRED_MARKDOWN_SECTIONS if section not in text]
    if f"# Модуль: {module_name}" not in text:
        missing_sections = [f"# Модуль: {module_name}", *missing_sections]

    forbidden_hits = []
    if "reasoning_details" in text:
        forbidden_hits.append("reasoning_details")
    if "OPENROUTER_API=" in text:
        forbidden_hits.append("OPENROUTER_API=")
    if api_key and api_key in text:
        forbidden_hits.append("api_key_value")
    if any(marker in text for marker in RAW_RESPONSE_MARKERS) and ("```json" in lower_text or "{" in text):
        forbidden_hits.append("raw_response_json")

    has_uncertainty_language = any(phrase in lower_text for phrase in UNCERTAINTY_PHRASES)
    return {
        "required_sections_present": not missing_sections and bool(text.strip()),
        "missing_sections": unique_strings(missing_sections),
        "forbidden_hits": forbidden_hits,
        "has_uncertainty_language": has_uncertainty_language,
    }


def validate_semantic_markdown(markdown_text: str, *, entity_count: int | None = None) -> dict[str, Any]:
    text = markdown_text or ""
    lower_text = text.lower()
    sections = split_markdown_sections(text)
    overconfident_claims: list[str] = []
    entity_count_wording_warnings: list[str] = []
    uncertainty_language = any(phrase in lower_text for phrase in UNCERTAINTY_PHRASES)
    factual_support_section_present = "## Фактическая опора" in sections
    structure_ok = True

    for heading in STRUCTURED_ANALYSIS_SECTIONS:
        section_text = sections.get(heading, "")
        if not section_text:
            structure_ok = False
            continue
        if not all(marker.lower() in section_text.lower() for marker in STRUCTURE_MARKERS):
            structure_ok = False

    for heading, section_text in sections.items():
        if heading == "## Фактическая опора":
            continue
        for line in section_text.splitlines():
            normalized_line = line.strip()
            if not normalized_line:
                continue
            lower_line = normalized_line.lower()
            if not any(pattern in lower_line for pattern in OVERCONFIDENT_PATTERNS):
                continue
            if any(phrase in lower_line for phrase in UNCERTAINTY_PHRASES):
                continue
            if lower_line.startswith("- факты") or lower_line.startswith("факты"):
                continue
            overconfident_claims.append(f"{heading}: {normalized_line[:200]}")

    if entity_count is not None and entity_count > 0:
        bad_entity_count_phrases = (
            "точное количество всех существующих сущностей неизвестно",
            "общее количество сущностей неизвестно",
        )
        for phrase in bad_entity_count_phrases:
            if phrase in lower_text:
                entity_count_wording_warnings.append(
                    "entity_count is present in factual docs, but markdown says the total entity count is unknown"
                )
                break

    problems = 0
    if overconfident_claims:
        problems += 1
    if entity_count_wording_warnings:
        problems += 1
    if not uncertainty_language:
        problems += 1
    if not factual_support_section_present:
        problems += 1
    if not structure_ok:
        problems += 1

    if problems == 0:
        verdict = "pass"
    elif problems == 1:
        verdict = "warning"
    else:
        verdict = "fail"

    return {
        "overconfident_claims": sorted(unique_strings(overconfident_claims)),
        "entity_count_wording_warnings": sorted(unique_strings(entity_count_wording_warnings)),
        "has_uncertainty_language": uncertainty_language,
        "has_facts_interpretations_unknown_structure": structure_ok,
        "factual_support_section_present": factual_support_section_present,
        "verdict": verdict,
    }


def resolve_docs_root(plan: dict[str, Any], plan_path: Path) -> Path:
    candidates: list[Path] = []
    raw_docs_path = str(plan.get("docs_path") or "").strip()
    if raw_docs_path:
        docs_path = Path(raw_docs_path)
        if docs_path.is_absolute():
            candidates.append(docs_path)
        else:
            candidates.append(plan_path.parent.resolve())
            candidates.append((Path.cwd() / docs_path).resolve())
            candidates.append((plan_path.parent / docs_path).resolve())
    candidates.append(plan_path.parent.resolve())

    for candidate in unique_paths(candidates):
        if candidate.exists() and candidate.is_dir():
            if docs_root_matches_plan(candidate, plan):
                return candidate
    for candidate in unique_paths(candidates):
        if candidate.exists() and candidate.is_dir():
            return candidate
    raise ValueError("Could not resolve docs directory from explain-plan.")


def docs_root_matches_plan(candidate: Path, plan: dict[str, Any]) -> bool:
    expected_paths: list[str] = []
    for module in plan.get("modules", []):
        if isinstance(module, dict) and module.get("module_doc_path"):
            expected_paths.append(str(module["module_doc_path"]))
            break
    global_docs = plan.get("global_docs", {})
    if isinstance(global_docs, dict):
        for ref in global_docs.values():
            if isinstance(ref, dict) and ref.get("path"):
                expected_paths.append(str(ref["path"]))
                break
    if not expected_paths:
        return True
    return any((candidate / Path(path.replace("\\", "/"))).is_file() for path in expected_paths)


def load_context_document(docs_root: Path, relative_path: str, *, label: str, required: bool) -> dict[str, Any]:
    normalized_path = relative_path.replace("\\", "/")
    resolved_path = (docs_root / Path(normalized_path)).resolve()
    docs_root_resolved = docs_root.resolve()
    try:
        resolved_path.relative_to(docs_root_resolved)
    except ValueError as exc:
        raise ValueError(f"Context path escapes docs root: {normalized_path}") from exc
    if not resolved_path.is_file():
        raise ValueError(f"Context file is missing: {normalized_path}")
    content = resolved_path.read_text(encoding="utf-8")
    return {
        "path": normalized_path,
        "label": label,
        "required": required,
        "content": content,
        "estimated_tokens": estimate_tokens(content),
    }


def enrich_document(document: dict[str, Any], *, priority: int, score: int) -> dict[str, Any]:
    payload = dict(document)
    payload["priority"] = priority
    payload["score"] = score
    payload["reduced"] = False
    return payload


def render_context_documents(documents: list[dict[str, Any]], function_index_summary: str | None) -> str:
    parts: list[str] = []
    for document in documents:
        parts.append(f"### {document['label']}: {document['path']}\n{document['content'].strip()}")
    if function_index_summary:
        parts.append(f"### function_index_summary\n{function_index_summary}")
    return "\n\n".join(part for part in parts if part).strip()


def reduce_context_documents(
    plan: dict[str, Any],
    module_target: dict[str, Any],
    documents: list[dict[str, Any]],
    function_index_summary: str | None,
    max_input_tokens: int,
    *,
    docs_root: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected: list[dict[str, Any]] = []
    dropped: list[str] = []

    for label in ("module_doc", "coverage_report", "dependency_map"):
        document = next((item for item in documents if item["label"] == label), None)
        if document is not None:
            selected.append(maybe_reduce_document(document))

    selected_paths = {document["path"] for document in selected}
    for document in documents:
        if document["path"] in selected_paths or document["label"].startswith("file_doc:"):
            continue
        dropped.append(document["path"])

    for document in [item for item in documents if item["label"].startswith("file_doc:")]:
        candidate_document = maybe_reduce_document(document)
        candidate_selection = selected + [candidate_document]
        candidate_bundle = finalize_context_bundle(
            plan,
            module_target,
            docs_root,
            candidate_selection,
            function_index_summary,
            max_input_tokens,
        )
        if max_input_tokens <= 0 or candidate_bundle["token_budget"]["estimated_prompt_tokens_with_margin"] <= max_input_tokens:
            selected.append(candidate_document)
        else:
            dropped.append(document["path"])

    retained_paths = {document["path"] for document in selected}
    dropped.extend(document["path"] for document in documents if document["path"] not in retained_paths)
    return selected, sorted(unique_strings(dropped))


def maybe_reduce_document(document: dict[str, Any]) -> dict[str, Any]:
    reduced = dict(document)
    summary_limit = document_summary_limit(document)
    if summary_limit is None:
        return reduced

    original_content = str(document["content"])
    truncated = truncate_context_content(original_content, summary_limit)
    if truncated != document["content"]:
        reduced["content"] = truncated
        reduced["estimated_tokens"] = estimate_tokens(truncated)
        reduced["reduced"] = True
        reduced["truncation"] = {
            "path": str(document["path"]),
            "truncation_mode": "char_limit",
            "original_chars": len(original_content),
            "retained_chars": len(truncated),
            "original_estimated_tokens": estimate_tokens(original_content),
            "retained_estimated_tokens": estimate_tokens(truncated),
        }
    return reduced


def document_summary_limit(document: dict[str, Any]) -> int | None:
    label = str(document.get("label") or "")
    if label.startswith("file_doc:"):
        return DOCUMENT_SUMMARY_LIMITS["file_doc"]
    return DOCUMENT_SUMMARY_LIMITS.get(label)


def truncate_context_content(content: str, limit: int) -> str:
    normalized = content.strip()
    if len(normalized) <= limit:
        return content
    excerpt = normalized[:limit]
    if "\n" in excerpt:
        excerpt = excerpt.rsplit("\n", 1)[0]
    excerpt = excerpt.rstrip()
    return excerpt + "\n\n[Context reduced for token budget.]"


def normalize_generated_markdown(markdown_text: str, module_name: str) -> str:
    text = (markdown_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        raise ValueError("LLM returned empty content.")
    if f"# Модуль: {module_name}" not in text:
        text = f"# Модуль: {module_name}\n\n{text}"
    return text


def sanitize_generated_markdown(markdown_text: str, *, api_key: str | None = None) -> str:
    text = markdown_text.replace("reasoning_details", "[redacted_reasoning_field]")
    text = text.replace("OPENROUTER_API=", "OPENROUTER_API_REDACTED=")
    if api_key:
        text = text.replace(api_key, "[redacted_api_key]")
    return text


def normalize_entity_count_wording(markdown_text: str, module_target: dict[str, Any]) -> str:
    entity_count = parse_optional_int(module_target.get("entity_count"))
    if entity_count is None or entity_count <= 0:
        return markdown_text
    replacements = {
        "точное количество всех существующих сущностей неизвестно": (
            "статистическое количество сущностей известно из factual docs; "
            "полный детальный список сущностей может быть усечен в предоставленном контексте"
        ),
        "Точное количество всех существующих сущностей неизвестно": (
            "Статистическое количество сущностей известно из factual docs; "
            "полный детальный список сущностей может быть усечен в предоставленном контексте"
        ),
    }
    text = markdown_text
    for needle, replacement in replacements.items():
        text = text.replace(needle, replacement)
    return text


def metadata_output_path(output_path: Path) -> Path:
    if output_path.parent.name == "modules" and output_path.parent.parent:
        return output_path.parent.parent / "llm-runs" / f"{output_path.stem}.metadata.json"
    return output_path.parent / f"{output_path.stem}.metadata.json"


def build_token_budget(
    *,
    context_bundle: dict[str, Any],
    max_input_tokens: int,
    usage: dict[str, Any] | None,
    override_used: bool,
) -> dict[str, Any]:
    actual_prompt_tokens = normalize_usage_metric(usage, "prompt_tokens")
    actual_completion_tokens = normalize_usage_metric(usage, "completion_tokens")
    actual_total_tokens = normalize_usage_metric(usage, "total_tokens")
    reduction_plan = context_bundle.get("reduction_plan")
    if not isinstance(reduction_plan, ReductionPlan):
        estimated = context_bundle["token_budget"]
        reduction_plan = ReductionPlan(
            pre_reduction_estimated_context_tokens=estimated["estimated_context_tokens"],
            pre_reduction_estimated_prompt_tokens=estimated["estimated_prompt_tokens"],
            pre_reduction_estimated_prompt_tokens_with_margin=estimated[
                "estimated_prompt_tokens_with_margin"
            ],
            post_reduction_estimated_context_tokens=estimated["estimated_context_tokens"],
            post_reduction_estimated_prompt_tokens=estimated["estimated_prompt_tokens"],
            post_reduction_estimated_prompt_tokens_with_margin=estimated[
                "estimated_prompt_tokens_with_margin"
            ],
            max_input_tokens=max_input_tokens,
            reduction_was_required=False,
            reduction_applied=False,
            reduction_reason=None,
            retained_context_files=list(context_bundle.get("context_files", [])),
            dropped_context_files=[],
            truncated_context_files=[],
            notes=[],
            context_fingerprint=build_context_fingerprint(context_bundle.get("context_text", "")),
        )

    if actual_prompt_tokens is not None:
        prompt_budget_exceeded = actual_prompt_tokens > max_input_tokens if max_input_tokens > 0 else False
        prompt_budget_delta = (actual_prompt_tokens - max_input_tokens) if max_input_tokens > 0 else 0
    else:
        predicted = reduction_plan.post_reduction_estimated_prompt_tokens_with_margin
        prompt_budget_exceeded = None
        prompt_budget_delta = (predicted - max_input_tokens) if max_input_tokens > 0 else 0

    return {
        "estimation_method": "approx_chars_div_4",
        "estimated_context_tokens": reduction_plan.post_reduction_estimated_context_tokens,
        "estimated_prompt_tokens": reduction_plan.post_reduction_estimated_prompt_tokens,
        "prompt_safety_margin_tokens": (
            reduction_plan.post_reduction_estimated_prompt_tokens_with_margin
            - reduction_plan.post_reduction_estimated_prompt_tokens
        ),
        "estimated_prompt_tokens_with_margin": (
            reduction_plan.post_reduction_estimated_prompt_tokens_with_margin
        ),
        "pre_reduction_estimated_context_tokens": (
            reduction_plan.pre_reduction_estimated_context_tokens
        ),
        "pre_reduction_estimated_prompt_tokens": (
            reduction_plan.pre_reduction_estimated_prompt_tokens
        ),
        "pre_reduction_estimated_prompt_tokens_with_margin": (
            reduction_plan.pre_reduction_estimated_prompt_tokens_with_margin
        ),
        "post_reduction_estimated_context_tokens": (
            reduction_plan.post_reduction_estimated_context_tokens
        ),
        "post_reduction_estimated_prompt_tokens": (
            reduction_plan.post_reduction_estimated_prompt_tokens
        ),
        "post_reduction_estimated_prompt_tokens_with_margin": (
            reduction_plan.post_reduction_estimated_prompt_tokens_with_margin
        ),
        "actual_prompt_tokens": actual_prompt_tokens,
        "actual_completion_tokens": actual_completion_tokens,
        "actual_total_tokens": actual_total_tokens,
        "max_input_tokens": max_input_tokens,
        "prompt_budget_exceeded": prompt_budget_exceeded,
        "prompt_budget_delta": prompt_budget_delta,
        "reduction_was_required": reduction_plan.reduction_was_required,
        "reduction_applied": reduction_plan.reduction_applied,
        "reduction_reason": reduction_plan.reduction_reason,
        "override_used": override_used,
    }


def normalize_usage_metric(usage: dict[str, Any] | None, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_global_doc_path(plan: dict[str, Any], global_key: str) -> str | None:
    global_docs = plan.get("global_docs", {})
    if not isinstance(global_docs, dict):
        return None
    ref = global_docs.get(global_key)
    if not isinstance(ref, dict) or not ref.get("exists") or not ref.get("path"):
        return None
    return str(ref["path"]).replace("\\", "/")


def parse_file_index_counts(markdown_text: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if len(columns) < 4:
            continue
        if columns[0].lower() == "file path":
            continue
        path = columns[0]
        counts[path] = {
            "entity_count": parse_int(columns[1]),
            "import_count": parse_int(columns[2]),
        }
    return counts


def parse_int(text: str) -> int:
    digits = re.sub(r"[^\d-]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def split_markdown_sections(markdown_text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_heading = "#"
    sections[current_heading] = []
    for line in markdown_text.splitlines():
        if line.startswith("## "):
            current_heading = line.strip()
            sections.setdefault(current_heading, [])
            continue
        sections.setdefault(current_heading, []).append(line)
    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def calculate_prompt_safety_margin(estimated_prompt_tokens: int) -> int:
    percentage_margin = (estimated_prompt_tokens * int(PROMPT_SAFETY_MARGIN_RATIO * 100) + 99) // 100
    return max(PROMPT_SAFETY_MARGIN_MIN_TOKENS, percentage_margin)


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(text: str) -> int:
    return max(0, (len(text) + 3) // 4)


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def unique_paths(values: list[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for value in values:
        marker = str(value)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(value)
    return ordered


def ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"
