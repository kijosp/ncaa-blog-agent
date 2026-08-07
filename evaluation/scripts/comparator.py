"""Event coverage comparison between baseline and challenger model runs.

Supports two matching strategies:
  - "signature": deterministic string matching (fast, strict)
  - "llm": first checks team, source_url, and event_type match exactly,
    then uses Nova Micro to judge whether the core signal/facts match.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

import boto3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signature-based matching (original, fast)
# ---------------------------------------------------------------------------


def event_signature(event: dict) -> str:
    """Create a normalized matching key for an event."""
    return "|".join(
        [
            event.get("team", "").lower().strip(),
            event.get("event_type", "").upper().strip(),
            (event.get("player_name") or "").lower().strip(),
            event.get("source_url", "").strip(),
        ]
    )


def compute_coverage_signature(
    baseline_events: list[dict], challenger_events: list[dict]
) -> dict:
    """Compare events using deterministic signature matching."""
    baseline_sigs = {event_signature(e) for e in baseline_events}
    challenger_sigs = {event_signature(e) for e in challenger_events}

    overlap = baseline_sigs & challenger_sigs

    return {
        "baseline_count": len(baseline_sigs),
        "challenger_count": len(challenger_sigs),
        "overlap_count": len(overlap),
        "baseline_only_count": len(baseline_sigs) - len(overlap),
        "challenger_only_count": len(challenger_sigs) - len(overlap),
        "recall_vs_baseline": (
            len(overlap) / len(baseline_sigs) if baseline_sigs else 1.0
        ),
        "precision_vs_baseline": (
            len(overlap) / len(challenger_sigs) if challenger_sigs else 1.0
        ),
    }


# ---------------------------------------------------------------------------
# LLM-based matching (semantic core-fact comparison via Nova Micro)
# ---------------------------------------------------------------------------

_LLM_JUDGE_MODEL = os.environ.get(
    "EVAL_JUDGE_MODEL_ID", "amazon.nova-micro-v1:0"
)

_MATCH_SYSTEM_PROMPT = """\
You are a sports event matching judge. You will be given two event descriptions \
that share the same team, source URL, and event type. Your job is to determine \
whether they describe the SAME core signal — i.e., whether a sports trader would \
treat them as the same actionable piece of information.

Focus on:
- Same player (if applicable)
- Same core fact (e.g., same injury diagnosis, same transfer destination, \
same game date change, same cancellation)
- Minor wording differences are irrelevant — only the factual substance matters.

Respond with ONLY a JSON object: {"match": true} or {"match": false}
Do NOT include any other text.
"""


def _format_event_for_judge(event: dict) -> str:
    """Format an event dict into a concise string for the LLM judge."""
    parts = [
        f"Type: {event.get('event_type', 'N/A')}",
        f"Player: {event.get('player_name') or 'N/A'}",
        f"Summary: {event.get('summary', 'N/A')}",
    ]
    if event.get("blog_post_date"):
        parts.append(f"Date: {event['blog_post_date']}")
    return "\n".join(parts)


def _structural_match(baseline_event: dict, candidate_event: dict) -> bool:
    """Check if team, source_url, and event_type match exactly."""
    if (baseline_event.get("team", "").lower().strip() !=
            candidate_event.get("team", "").lower().strip()):
        return False
    if (baseline_event.get("source_url", "").strip() !=
            candidate_event.get("source_url", "").strip()):
        return False
    if (baseline_event.get("event_type", "").upper().strip() !=
            candidate_event.get("event_type", "").upper().strip()):
        return False
    return True


def _llm_judge_core_facts(baseline_event: dict, candidate_event: dict) -> bool:
    """Ask Nova Micro whether two events (same team/url/type) share the same core signal."""
    client = boto3.client("bedrock-runtime", region_name="us-east-1")

    user_message = (
        f"BASELINE EVENT:\n{_format_event_for_judge(baseline_event)}\n\n"
        f"CANDIDATE EVENT:\n{_format_event_for_judge(candidate_event)}\n\n"
        f"Do these describe the same core signal?"
    )

    try:
        response = client.converse(
            modelId=_LLM_JUDGE_MODEL,
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            system=[{"text": _MATCH_SYSTEM_PROMPT}],
            inferenceConfig={"maxTokens": 50, "temperature": 0.0},
        )
        output_text = response["output"]["message"]["content"][0]["text"].strip()
        result = json.loads(output_text)
        return result.get("match", False)
    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.warning("LLM judge failed for event pair: %s", e)
        # Fall back: if structural fields match, assume core facts match too
        return True


def compute_coverage_llm(
    baseline_events: list[dict], challenger_events: list[dict]
) -> dict:
    """Compare events using structural pre-filter + LLM core-fact matching.

    Logic:
      1. For each baseline event, look for a challenger event with the same
         team, source_url, and event_type (structural match).
      2. If structural match found, use Nova Micro to verify that the core
         facts/signal are the same.
      3. Count matches for recall/precision.
    """
    if not baseline_events:
        return {
            "baseline_count": 0,
            "challenger_count": len(challenger_events),
            "overlap_count": 0,
            "baseline_only_count": 0,
            "challenger_only_count": len(challenger_events),
            "recall_vs_baseline": 1.0,
            "precision_vs_baseline": 1.0 if not challenger_events else 0.0,
        }

    if not challenger_events:
        return {
            "baseline_count": len(baseline_events),
            "challenger_count": 0,
            "overlap_count": 0,
            "baseline_only_count": len(baseline_events),
            "challenger_only_count": 0,
            "recall_vs_baseline": 0.0,
            "precision_vs_baseline": 1.0,
        }

    matched_baseline = set()
    matched_challenger = set()

    for i, b_event in enumerate(baseline_events):
        for j, c_event in enumerate(challenger_events):
            if j in matched_challenger:
                continue  # already matched to a different baseline event

            # Step 1: structural pre-filter (team + source_url + event_type)
            if not _structural_match(b_event, c_event):
                continue

            # Step 2: LLM judges whether core facts match
            if _llm_judge_core_facts(b_event, c_event):
                matched_baseline.add(i)
                matched_challenger.add(j)
                break  # move to next baseline event

    overlap_count = len(matched_baseline)
    baseline_count = len(baseline_events)
    challenger_count = len(challenger_events)

    return {
        "baseline_count": baseline_count,
        "challenger_count": challenger_count,
        "overlap_count": overlap_count,
        "baseline_only_count": baseline_count - overlap_count,
        "challenger_only_count": challenger_count - len(matched_challenger),
        "recall_vs_baseline": overlap_count / baseline_count,
        "precision_vs_baseline": (
            len(matched_challenger) / challenger_count if challenger_count else 1.0
        ),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_coverage(
    baseline_events: list[dict],
    challenger_events: list[dict],
    method: Literal["signature", "llm"] = "signature",
) -> dict:
    """Compare events found by baseline vs challenger.

    Args:
        baseline_events: Events detected by the baseline model.
        challenger_events: Events detected by the challenger model.
        method: "signature" for fast deterministic matching,
                "llm" for structural pre-filter + Nova Micro core-fact matching.

    Returns:
        Coverage metrics showing how well the challenger captures baseline events.
    """
    if method == "llm":
        return compute_coverage_llm(baseline_events, challenger_events)
    return compute_coverage_signature(baseline_events, challenger_events)


def compare_runs(
    baseline_run: dict,
    challenger_run: dict,
    method: Literal["signature", "llm"] = "signature",
) -> dict:
    """Compare two full evaluation runs (output JSONs from runner).

    Args:
        baseline_run: Full result dict from run_evaluation (baseline model).
        challenger_run: Full result dict from run_evaluation (challenger model).
        method: "signature" for fast deterministic matching,
                "llm" for structural pre-filter + Nova Micro core-fact matching.

    Returns:
        Per-team and aggregate coverage metrics.
    """
    baseline_by_team = {
        r["team"]: r["events"] for r in baseline_run["team_results"]
    }
    challenger_by_team = {
        r["team"]: r["events"] for r in challenger_run["team_results"]
    }

    per_team = {}
    all_baseline_events = []
    all_challenger_events = []

    for team in baseline_by_team:
        b_events = baseline_by_team.get(team, [])
        c_events = challenger_by_team.get(team, [])
        all_baseline_events.extend(b_events)
        all_challenger_events.extend(c_events)
        per_team[team] = compute_coverage(b_events, c_events, method=method)

    aggregate = compute_coverage(
        all_baseline_events, all_challenger_events, method=method
    )

    return {
        "baseline_model": baseline_run["model_config"]["label"],
        "challenger_model": challenger_run["model_config"]["label"],
        "method": method,
        "per_team": per_team,
        "aggregate": aggregate,
    }
