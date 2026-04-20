from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION = "1.0"
EXPLAIN_PLAN_LAYOUT_VERSION = "1.0"
EXPECTED_DOCUMENTATION_LAYOUT_VERSION = "2.1"

DEFAULT_PROVIDER = "openrouter"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it"
DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL
DEFAULT_REASONING_ENABLED = True
DEFAULT_REASONING_DETAILS_POLICY = "preserve_if_returned_later_do_not_render"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_ENV = "OPENROUTER_API"
DEFAULT_API_KEY_ENV = OPENROUTER_API_ENV
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
    "Р§С‚Рѕ РёР·РІРµСЃС‚РЅРѕ",
    "РќР°Р·РЅР°С‡РµРЅРёРµ",
    "РљР°Рє СЂР°Р±РѕС‚Р°РµС‚",
    "РљРѕРЅС‚СѓСЂ РІР·Р°РёРјРѕРґРµР№СЃС‚РІРёСЏ",
    "РљР»СЋС‡РµРІС‹Рµ С„СѓРЅРєС†РёРё",
    "Р—Р°РІРёСЃРёРјРѕСЃС‚Рё",
    "Р§С‚Рѕ РЅРµ СѓРґР°Р»РѕСЃСЊ РѕРїСЂРµРґРµР»РёС‚СЊ",
    "РЈСЂРѕРІРµРЅСЊ СѓРІРµСЂРµРЅРЅРѕСЃС‚Рё",
    "Р¤Р°РєС‚РёС‡РµСЃРєР°СЏ РѕРїРѕСЂР°",
]

FORBIDDEN_CLAIMS_WITHOUT_SUPPORT = [
    "business_purpose",
    "runtime_behavior",
    "external_api_usage",
    "side_effects",
    "performance_claims",
    "security_claims",
]


@dataclass(frozen=True, slots=True)
class OpenRouterConfig:
    provider: str = DEFAULT_PROVIDER
    base_url: str = OPENROUTER_BASE_URL
    model: str = DEFAULT_OPENROUTER_MODEL
    api_key_env: str = OPENROUTER_API_ENV
    api_key: str | None = None
    reasoning_enabled: bool = DEFAULT_REASONING_ENABLED

    @property
    def key_present(self) -> bool:
        return bool(self.api_key)


def load_dotenv_file(path: Path | None = None) -> dict[str, str]:
    dotenv_path = path or (Path.cwd() / ".env")
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        normalized_value = _strip_optional_quotes(value.strip())
        loaded[normalized_key] = normalized_value
        os.environ.setdefault(normalized_key, normalized_value)
    return loaded


def get_openrouter_api_key(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> str | None:
    load_dotenv_file(dotenv_path)
    value = None
    if env is not None:
        value = env.get(OPENROUTER_API_ENV)
    if value is None:
        value = os.environ.get(OPENROUTER_API_ENV)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def build_openrouter_config(
    *,
    model: str | None = None,
    reasoning_enabled: bool = DEFAULT_REASONING_ENABLED,
    dotenv_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    api_key: str | None = None,
) -> OpenRouterConfig:
    resolved_api_key = api_key if api_key is not None else get_openrouter_api_key(env=env, dotenv_path=dotenv_path)
    return OpenRouterConfig(
        provider=DEFAULT_PROVIDER,
        base_url=OPENROUTER_BASE_URL,
        model=model or DEFAULT_OPENROUTER_MODEL,
        api_key_env=OPENROUTER_API_ENV,
        api_key=resolved_api_key,
        reasoning_enabled=reasoning_enabled,
    )


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
