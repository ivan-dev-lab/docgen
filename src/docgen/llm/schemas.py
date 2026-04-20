from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocsManifestInfo:
    path: str
    documentation_layout_version: str | None
    generated_file_count: int
    module_page_count: int
    file_page_count: int


@dataclass
class ModelPlan:
    provider: str
    default_model: str
    reasoning_enabled: bool
    reasoning_details_policy: str
    api_key_env: str
    network_enabled_in_stage_3a: bool


@dataclass
class BudgetPolicy:
    strategy: str
    max_input_tokens_per_module: int
    max_output_tokens_per_module: int
    max_modules_per_run: int
    token_estimation_method: str
    token_estimates_are_exact: bool


@dataclass
class ContextPolicy:
    allowed_context_roots: list[str]
    forbidden_context: list[str]
    include_module_doc: bool
    include_related_file_docs: bool
    include_dependency_map: bool
    include_coverage_report: bool
    include_function_index: str


@dataclass
class PromptTemplateRef:
    system_prompt_name: str
    user_template_name: str
    output_contract_name: str


@dataclass
class PromptRegistry:
    module_explanation: PromptTemplateRef
    architecture_synthesis: PromptTemplateRef
    verification: PromptTemplateRef


@dataclass
class GlobalDocRef:
    path: str
    exists: bool


@dataclass
class FileDocRef:
    source_file: str
    doc_path: str
    exists: bool


@dataclass
class LLMCompletionResult:
    provider: str
    model: str
    content: str
    reasoning: Any | None
    reasoning_details_present: bool
    usage: dict[str, Any] | None
    raw_response_type: str
    finish_reason: str | None
    error: str | None = None


@dataclass
class ModuleExplainTarget:
    name: str
    type: str
    module_page_role: str
    module_doc_path: str
    module_doc_exists: bool
    source_files: list[str]
    test_files: list[str]
    file_doc_paths: list[FileDocRef]
    entity_count: int
    dependency_count: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    budget_status: str
    priority: str
    explain_mode: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    context_paths: list[str] = field(default_factory=list)
    context_reduction_reason: str | None = None
    planned_output_path: str = ""


@dataclass
class ConsistencyDiagnostics:
    analysis_files_with_facts: list[str]
    rendered_file_pages: list[str]
    missing_file_pages: list[str]
    analysis_render_mismatches: list[str]
    stale_docs_detected: bool
    manifest_has_file_pages: bool
    manifest_has_module_pages: bool


@dataclass
class OutputContract:
    module_explanation_required_sections: list[str]
    forbidden_claims_without_support: list[str]


@dataclass
class VerificationPolicy:
    require_factual_support_section: bool
    require_uncertainty_section: bool
    flag_unsupported_runtime_claims: bool
    flag_business_claims_without_facts: bool


@dataclass
class ExplainPlan:
    schema_version: str
    generated_at: str
    analysis_path: str
    docs_path: str
    docs_manifest: DocsManifestInfo
    model_plan: ModelPlan
    budget_policy: BudgetPolicy
    context_policy: ContextPolicy
    prompt_registry: PromptRegistry
    global_docs: dict[str, GlobalDocRef]
    modules: list[ModuleExplainTarget]
    consistency: ConsistencyDiagnostics
    output_contract: OutputContract
    verification_policy: VerificationPolicy
    warnings: list[str] = field(default_factory=list)
