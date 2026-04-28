from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from docgen.ui_run_diff import RunDiffError, build_run_diff  # noqa: E402


class UiRunDiffTests(unittest.TestCase):
    def test_generation_diff_detects_added_removed_changed_unchanged_and_usage_delta(self) -> None:
        run_a = {
            "kind": "generation",
            "run_id": "gen-a",
            "usage_totals": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "generated_count": 2,
            "skipped_cached_count": 0,
            "failed_count": 1,
            "results": [
                {"module": "same", "status": "generated", "cache_hit": False, "usage": {"total_tokens": 1}},
                {"module": "changed", "status": "generated", "cache_hit": False, "usage": {"total_tokens": 10}},
                {"module": "removed", "status": "failed_generation", "usage": {"total_tokens": 2}},
            ],
        }
        run_b = {
            "kind": "generation",
            "run_id": "gen-b",
            "usage_totals": {"prompt_tokens": 15, "completion_tokens": 7, "total_tokens": 22},
            "generated_count": 3,
            "skipped_cached_count": 1,
            "failed_count": 0,
            "results": [
                {"module": "same", "status": "generated", "cache_hit": False, "usage": {"total_tokens": 1}},
                {"module": "changed", "status": "skipped_cached", "cache_hit": True, "usage": {"total_tokens": 4}},
                {"module": "added", "status": "generated", "usage": {"total_tokens": 3}},
            ],
        }

        diff = build_run_diff("generation", run_a, run_b)
        statuses = {item["module"]: item for item in diff["module_diffs"]}

        self.assertEqual(statuses["added"]["change_status"], "added")
        self.assertEqual(statuses["removed"]["change_status"], "removed")
        self.assertEqual(statuses["changed"]["change_status"], "changed")
        self.assertEqual(statuses["same"]["change_status"], "unchanged")
        self.assertIn("status", statuses["changed"]["changed_fields"])
        self.assertEqual(statuses["changed"]["usage_delta"]["total_tokens"], -6)
        self.assertEqual(diff["summary"]["usage_total_delta"]["total_tokens"], 7)
        self.assertEqual(diff["summary"]["generated_count_delta"], 1)

    def test_verification_diff_detects_verdict_direction_issue_and_usage_deltas(self) -> None:
        run_a = {
            "kind": "verification",
            "run_id": "ver-a",
            "usage_totals": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            "results": [
                {
                    "module": "improved",
                    "status": "verified_fail",
                    "verdict": "fail",
                    "weak_claims_count": 2,
                    "unsupported_claims_count": 1,
                    "usage": {"total_tokens": 10},
                },
                {"module": "worsened", "status": "verified_pass", "verdict": "pass", "usage": {"total_tokens": 3}},
                {"module": "same", "status": "verified_warning", "verdict": "warning"},
            ],
        }
        run_b = {
            "kind": "verification",
            "run_id": "ver-b",
            "usage_totals": {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30},
            "results": [
                {
                    "module": "improved",
                    "status": "verified_warning",
                    "verdict": "warning",
                    "weak_claims_count": 1,
                    "unsupported_claims_count": 1,
                    "usage": {"total_tokens": 6},
                },
                {"module": "worsened", "status": "verified_fail", "verdict": "fail", "usage": {"total_tokens": 9}},
                {"module": "same", "status": "verified_warning", "verdict": "warning"},
            ],
        }

        diff = build_run_diff("verification", run_a, run_b)
        modules = {item["module"]: item for item in diff["module_diffs"]}

        self.assertEqual(modules["improved"]["verdict_direction"], "improved")
        self.assertEqual(modules["worsened"]["verdict_direction"], "worsened")
        self.assertEqual(modules["same"]["verdict_direction"], "unchanged")
        self.assertEqual(modules["improved"]["issue_count_delta"]["weak_claims"], -1)
        self.assertEqual(modules["worsened"]["usage_delta"]["total_tokens"], 6)
        self.assertEqual(diff["summary"]["verdict_improved_count"], 1)
        self.assertEqual(diff["summary"]["verdict_worsened_count"], 1)
        self.assertEqual(diff["summary"]["usage_total_delta"]["total_tokens"], 8)

    def test_kind_mismatch_is_rejected(self) -> None:
        with self.assertRaises(RunDiffError):
            build_run_diff("generation", {"kind": "generation", "results": []}, {"kind": "verification", "results": []})


if __name__ == "__main__":
    unittest.main()
