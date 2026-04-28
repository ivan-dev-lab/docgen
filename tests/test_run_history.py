from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.llm.run_history import build_ops_summary  # noqa: E402


class RunHistoryTests(unittest.TestCase):
    def test_ops_summary_cache_hit_rate_is_computed_from_current_manifests(self) -> None:
        summary = build_ops_summary(
            {
                "run_id": "generation-run",
                "total_modules_selected": 4,
                "generated_count": 2,
                "skipped_cached_count": 1,
                "skipped_by_plan_count": 1,
                "failed_count": 1,
                "usage_totals": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                "warnings": [],
            },
            {
                "run_id": "verification-run",
                "total_modules_selected": 2,
                "verified_count": 1,
                "warning_count": 0,
                "failed_count": 0,
                "skipped_cached_count": 1,
                "skipped_missing_enhanced_count": 3,
                "usage_totals": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                "warnings": [],
            },
            {"runs": [{"run_id": "generation-run"}]},
            {"runs": [{"run_id": "verification-run"}]},
        )

        self.assertEqual(summary["latest_generation_cache_hit_rate"], 0.25)
        self.assertEqual(summary["latest_verification_cache_hit_rate"], 0.5)
        self.assertEqual(summary["latest_generation_usage_totals"]["total_tokens"], 12)
        self.assertEqual(summary["latest_verification_usage_totals"]["total_tokens"], 6)
        self.assertEqual(summary["generation_history_count"], 1)
        self.assertEqual(summary["verification_history_count"], 1)


if __name__ == "__main__":
    unittest.main()
