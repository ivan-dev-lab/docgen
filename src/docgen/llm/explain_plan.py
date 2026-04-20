from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, prompts
from .context_builder import (
    build_analysis_files_with_facts,
    build_effective_doc_maps,
    build_global_doc_refs,
    build_module_target,
    ensure_analysis_bundle,
    ensure_docs_inputs,
    module_target_sort_key,
    module_target_to_dict,
)
from .schemas import (
    BudgetPolicy,
    ConsistencyDiagnostics,
    ContextPolicy,
    DocsManifestInfo,
    ExplainPlan,
    ModelPlan,
    OutputContract,
    PromptRegistry,
    PromptTemplateRef,
    VerificationPolicy,
)


def write_explain_plan(
    analysis_dir: Path,
    docs_dir: Path,
    output_path: Path,
    *,
    analysis_path_label: str | None = None,
    docs_path_label: str | None = None,
) -> Path:
    explain_plan = build_explain_plan(
        analysis_dir,
        docs_dir,
        analysis_path_label=analysis_path_label,
        docs_path_label=docs_path_label,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(explain_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def build_explain_plan(
    analysis_dir: Path,
    docs_dir: Path,
    *,
    analysis_path_label: str | None = None,
    docs_path_label: str | None = None,
) -> dict[str, Any]:
    bundle = ensure_analysis_bundle(analysis_dir)
    docs_manifest_payload, doc_warnings = ensure_docs_inputs(docs_dir)
    global_doc_refs, global_doc_warnings = build_global_doc_refs(docs_dir)

    modules_payload = [
        module
        for module in bundle["module-candidates.json"].get("candidates", [])
        if isinstance(module, dict)
    ]
    entities = [
        entity
        for entity in bundle["function-index.json"].get("entities", [])
        if isinstance(entity, dict)
    ]
    imports = [
        import_entry
        for import_entry in bundle["dependency-graph.json"].get("imports", [])
        if isinstance(import_entry, dict)
    ]
    analysis_files_with_facts = build_analysis_files_with_facts(entities, imports)
    analysis_files_with_facts_set = set(analysis_files_with_facts)
    (
        file_doc_map,
        module_doc_map,
        manifest_has_file_pages,
        manifest_has_module_pages,
        manifest_map_warnings,
        manifest_map_mismatches,
    ) = build_effective_doc_maps(bundle, docs_manifest_payload, docs_dir)

    has_non_test_modules = any(
        build_module_page_role(module.get("type")) != "test"
        for module in modules_payload
    )

    module_targets = []
    analysis_render_mismatches = list(manifest_map_mismatches)
    plan_warnings = list(doc_warnings) + list(global_doc_warnings) + list(manifest_map_warnings)
    for module in modules_payload:
        target, target_mismatches = build_module_target(
            module=module,
            docs_dir=docs_dir,
            module_doc_map=module_doc_map,
            file_doc_map=file_doc_map,
            global_doc_refs=global_doc_refs,
            entities=entities,
            imports=imports,
            has_non_test_modules=has_non_test_modules,
            analysis_files_with_facts=analysis_files_with_facts_set,
            manifest_has_module_pages=manifest_has_module_pages,
        )
        module_targets.append(target)
        plan_warnings.extend(target.warnings)
        analysis_render_mismatches.extend(target_mismatches)

    module_targets.sort(key=module_target_sort_key)
    consistency, consistency_warnings = build_consistency_diagnostics(
        analysis_summary=bundle["analysis-summary.json"],
        docs_manifest_payload=docs_manifest_payload,
        analysis_files_with_facts=analysis_files_with_facts,
        file_doc_map=file_doc_map,
        manifest_has_file_pages=manifest_has_file_pages,
        manifest_has_module_pages=manifest_has_module_pages,
        analysis_render_mismatches=analysis_render_mismatches,
    )
    plan_warnings.extend(consistency.analysis_render_mismatches)
    plan_warnings.extend(consistency_warnings)

    prompt_registry_dict = prompts.prompt_registry()
    explain_plan = ExplainPlan(
        schema_version=config.SCHEMA_VERSION,
        generated_at=timestamp_now(),
        analysis_path=analysis_path_label or str(analysis_dir),
        docs_path=docs_path_label or str(docs_dir),
        docs_manifest=DocsManifestInfo(
            path="doc-manifest.json",
            documentation_layout_version=docs_manifest_payload.get("documentation_layout_version"),
            generated_file_count=int(docs_manifest_payload.get("generated_file_count") or 0),
            module_page_count=int(docs_manifest_payload.get("module_page_count") or 0),
            file_page_count=int(docs_manifest_payload.get("file_page_count") or 0),
        ),
        model_plan=ModelPlan(
            provider=config.DEFAULT_PROVIDER,
            default_model=config.DEFAULT_MODEL,
            reasoning_enabled=config.DEFAULT_REASONING_ENABLED,
            reasoning_details_policy=config.DEFAULT_REASONING_DETAILS_POLICY,
            api_key_env=config.DEFAULT_API_KEY_ENV,
            network_enabled_in_stage_3a=config.NETWORK_ENABLED_IN_STAGE_3A,
        ),
        budget_policy=BudgetPolicy(
            strategy=config.BUDGET_STRATEGY,
            max_input_tokens_per_module=config.MAX_INPUT_TOKENS_PER_MODULE,
            max_output_tokens_per_module=config.MAX_OUTPUT_TOKENS_PER_MODULE,
            max_modules_per_run=config.MAX_MODULES_PER_RUN,
            token_estimation_method=config.TOKEN_ESTIMATION_METHOD,
            token_estimates_are_exact=config.TOKEN_ESTIMATES_ARE_EXACT,
        ),
        context_policy=ContextPolicy(
            allowed_context_roots=list(config.ALLOWED_CONTEXT_ROOTS),
            forbidden_context=list(config.FORBIDDEN_CONTEXT),
            include_module_doc=config.INCLUDE_MODULE_DOC,
            include_related_file_docs=config.INCLUDE_RELATED_FILE_DOCS,
            include_dependency_map=config.INCLUDE_DEPENDENCY_MAP,
            include_coverage_report=config.INCLUDE_COVERAGE_REPORT,
            include_function_index=config.INCLUDE_FUNCTION_INDEX,
        ),
        prompt_registry=PromptRegistry(
            module_explanation=PromptTemplateRef(**prompt_registry_dict["module_explanation"]),
            architecture_synthesis=PromptTemplateRef(**prompt_registry_dict["architecture_synthesis"]),
            verification=PromptTemplateRef(**prompt_registry_dict["verification"]),
        ),
        global_docs=global_doc_refs,
        modules=module_targets,
        consistency=consistency,
        output_contract=OutputContract(
            module_explanation_required_sections=list(config.MODULE_REQUIRED_SECTIONS),
            forbidden_claims_without_support=list(config.FORBIDDEN_CLAIMS_WITHOUT_SUPPORT),
        ),
        verification_policy=VerificationPolicy(
            require_factual_support_section=True,
            require_uncertainty_section=True,
            flag_unsupported_runtime_claims=True,
            flag_business_claims_without_facts=True,
        ),
        warnings=sorted(unique_strings(plan_warnings)),
    )
    payload = asdict(explain_plan)
    payload["global_docs"] = {
        key: asdict(value)
        for key, value in sorted(explain_plan.global_docs.items())
    }
    payload["modules"] = [module_target_to_dict(target) for target in module_targets]
    return payload


def build_module_page_role(candidate_type: Any) -> str:
    from ..renderer import determine_module_page_role

    return determine_module_page_role(str(candidate_type or "unknown"))


def module_identity(module: dict[str, Any]) -> str:
    return json.dumps(
        {
            "name": module.get("name"),
            "type": module.get("type"),
            "files": sorted(str(path) for path in module.get("files", [])),
            "source_files": sorted(str(path) for path in module.get("source_files", [])),
            "test_files": sorted(str(path) for path in module.get("test_files", [])),
            "related_files": sorted(str(path) for path in module.get("related_files", [])),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_consistency_diagnostics(
    *,
    analysis_summary: dict[str, Any],
    docs_manifest_payload: dict[str, Any],
    analysis_files_with_facts: list[str],
    file_doc_map: dict[str, dict[str, Any]],
    manifest_has_file_pages: bool,
    manifest_has_module_pages: bool,
    analysis_render_mismatches: list[str],
) -> tuple[ConsistencyDiagnostics, list[str]]:
    rendered_file_pages = sorted(unique_strings(list(file_doc_map)))
    missing_file_pages = sorted(set(analysis_files_with_facts) - set(rendered_file_pages))
    warnings: list[str] = []
    docs_timestamp = parse_timestamp(docs_manifest_payload.get("generated_at"))
    analysis_timestamp = parse_timestamp(analysis_summary.get("generated_at"))

    stale_docs_detected = False
    if docs_timestamp is not None and analysis_timestamp is not None:
        stale_docs_detected = docs_timestamp < analysis_timestamp
    elif docs_manifest_payload.get("generated_at") or analysis_summary.get("generated_at"):
        warnings.append("unable_to_compare_analysis_and_docs_timestamps")

    if len(missing_file_pages) >= 3:
        stale_docs_detected = True

    return (
        ConsistencyDiagnostics(
            analysis_files_with_facts=analysis_files_with_facts,
            rendered_file_pages=rendered_file_pages,
            missing_file_pages=missing_file_pages,
            analysis_render_mismatches=sorted(unique_strings(analysis_render_mismatches)),
            stale_docs_detected=stale_docs_detected,
            manifest_has_file_pages=manifest_has_file_pages,
            manifest_has_module_pages=manifest_has_module_pages,
        ),
        sorted(unique_strings(warnings)),
    )


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
