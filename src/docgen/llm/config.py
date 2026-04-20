from __future__ import annotations

SCHEMA_VERSION = "1.0"
EXPLAIN_PLAN_LAYOUT_VERSION = "1.0"
EXPECTED_DOCUMENTATION_LAYOUT_VERSION = "2.1"

DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it"
DEFAULT_REASONING_ENABLED = True
DEFAULT_REASONING_DETAILS_POLICY = "preserve_if_returned_later_do_not_render"
DEFAULT_API_KEY_ENV = "OPENROUTER_API"
NETWORK_ENABLED_IN_STAGE_3A = False

BUDGET_STRATEGY = "module_first"
MAX_INPUT_TOKENS_PER_MODULE = 24000
MAX_OUTPUT_TOKENS_PER_MODULE = 4000
MAX_MODULES_PER_RUN = 3
TOKEN_ESTIMATION_METHOD = "approx_chars_div_4"
TOKEN_ESTIMATES_ARE_EXACT = False

ALLOWED_CONTEXT_ROOTS = ["analysis_path", "docs_path"]
FORBIDDEN_CONTEXT = ["source_files_direct_read", "readme_direct_read", "network"]
INCLUDE_MODULE_DOC = True
INCLUDE_RELATED_FILE_DOCS = True
INCLUDE_DEPENDENCY_MAP = True
INCLUDE_COVERAGE_REPORT = True
INCLUDE_FUNCTION_INDEX = "summary_or_links_only"

REQUIRED_ANALYSIS_ARTIFACTS = (
    "inventory.json",
    "function-index.json",
    "dependency-graph.json",
    "module-candidates.json",
    "analysis-summary.json",
    "artifact-manifest.json",
    "coverage-report.json",
)

REQUIRED_DOC_ENTRIES = (
    "doc-manifest.json",
    "module-map.md",
    "dependency-map.md",
    "coverage-report.md",
    "functions/function-index.md",
    "files/index.md",
)

MODULE_REQUIRED_SECTIONS = [
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

FORBIDDEN_CLAIMS_WITHOUT_SUPPORT = [
    "business_purpose",
    "runtime_behavior",
    "external_api_usage",
    "side_effects",
    "performance_claims",
    "security_claims",
]
