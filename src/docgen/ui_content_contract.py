"""
Contract for UI content handling.

Markdown is display-only. Semantic UI state must come from JSON/ui-data/manifests.
Markdown bodies may be rendered, shown raw, or indexed as plain text at build-ui-data
time, but they must not be parsed for verdicts, statuses, counts, history state,
problem state, action state, or compare deltas.
"""

from __future__ import annotations

from dataclasses import dataclass


SEMANTIC_UI_STATE_SOURCES = (
    "docs/ui-data/*.json",
    "modules-index.json",
    "problems-index.json",
    "history-index.json",
    "history-runs.json",
    "current-state.json",
    "verification JSON reports",
    "batch manifests",
    "history manifests",
    "action-log.json",
)

FORBIDDEN_MARKDOWN_SEMANTIC_FIELDS = frozenset(
    {
        "action.domain_status",
        "action.status",
        "compare.change_status",
        "compare.issue_count_delta",
        "compare.run_delta",
        "compare.verdict_direction",
        "current.latest_generation_run",
        "current.latest_run_state",
        "current.latest_verification_run",
        "enhanced.generation_status",
        "enhanced.present",
        "factual.present",
        "history.latest_live_run",
        "history.result_status_counts",
        "history.state",
        "module.status",
        "problems.status",
        "problems.summary",
        "verification.batch_status",
        "verification.missing_factual_support_count",
        "verification.missing_uncertainty_count",
        "verification.present",
        "verification.structured_output_valid",
        "verification.unsupported_claims_count",
        "verification.verdict",
        "verification.verification_status",
        "verification.verifier_status",
        "verification.weak_claims_count",
    }
)

DISPLAY_CONTENT_FIELDS = frozenset({"text", "truncated"})


@dataclass(frozen=True)
class DisplayContent:
    """Artifact body prepared for UI display only, with no semantic status fields."""

    text: str
    truncated: bool = False


def display_content_from_text(text: str, *, limit: int | None = None) -> DisplayContent:
    if limit is not None and len(text) > limit:
        return DisplayContent(text=text[:limit] + "\n\n[truncated]", truncated=True)
    return DisplayContent(text=text, truncated=False)
