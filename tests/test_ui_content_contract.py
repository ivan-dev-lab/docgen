from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.ui_content_contract import (  # noqa: E402
    DISPLAY_CONTENT_FIELDS,
    FORBIDDEN_MARKDOWN_SEMANTIC_FIELDS,
    SEMANTIC_UI_STATE_SOURCES,
    display_content_from_text,
)


class UiContentContractTests(unittest.TestCase):
    def test_contract_declares_markdown_forbidden_semantic_fields(self) -> None:
        required = {
            "verification.verdict",
            "verification.verification_status",
            "verification.weak_claims_count",
            "verification.unsupported_claims_count",
            "verification.missing_factual_support_count",
            "verification.missing_uncertainty_count",
            "module.status",
            "enhanced.present",
            "factual.present",
            "history.state",
            "problems.summary",
            "action.domain_status",
            "compare.issue_count_delta",
        }

        self.assertTrue(required.issubset(FORBIDDEN_MARKDOWN_SEMANTIC_FIELDS))
        self.assertIn("modules-index.json", SEMANTIC_UI_STATE_SOURCES)
        self.assertIn("verification JSON reports", SEMANTIC_UI_STATE_SOURCES)

    def test_display_content_helper_exposes_no_semantic_state(self) -> None:
        content = display_content_from_text("warning fail pass\nweak claims: 999", limit=100)
        semantic_leaf_names = {field.rsplit(".", 1)[-1] for field in FORBIDDEN_MARKDOWN_SEMANTIC_FIELDS}

        self.assertEqual(set(content.__dataclass_fields__), DISPLAY_CONTENT_FIELDS)
        self.assertTrue(semantic_leaf_names.isdisjoint(content.__dataclass_fields__))
        self.assertIn("weak claims: 999", content.text)


if __name__ == "__main__":
    unittest.main()
