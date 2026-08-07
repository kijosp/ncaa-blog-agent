"""Select random teams from the blog registry export for evaluation."""

from __future__ import annotations

import json
import random
from pathlib import Path

REGISTRY_PATH = (
    Path(__file__).parent.parent.parent
    / "discovery"
    / "output"
    / "registry_export_20260716_011537.json"
)


def load_registry(path: Path | None = None) -> list[dict]:
    registry_file = path or REGISTRY_PATH
    with open(registry_file) as f:
        return json.load(f)


def sample_teams(
    n: int = 50,
    seed: int = 42,
    min_blogs: int = 1,
    min_non_rss: int = 0,
    registry_path: Path | None = None,
) -> list[dict]:
    """Select n random teams from the registry.

    Args:
        n: Number of teams to select.
        seed: Random seed for reproducibility.
        min_blogs: Minimum number of accessible blogs required.
        min_non_rss: Minimum number of accessible non-RSS blogs required.
            Set to 2+ to target teams that trigger the agent loop (expensive path).
        registry_path: Optional override for the registry JSON path.

    Returns:
        List of dicts with 'team', 'team_id', and 'blogs' (filtered to
        accessible + non-outdated only).
    """
    random.seed(seed)
    registry = load_registry(registry_path)

    eligible = []
    for entry in registry:
        accessible_blogs = [
            b for b in entry.get("blogs", [])
            if b.get("accessible") and b.get("recency_status") != "outdated"
        ]
        non_rss_blogs = [b for b in accessible_blogs if not b.get("rss_url")]

        if len(accessible_blogs) >= min_blogs and len(non_rss_blogs) >= min_non_rss:
            eligible.append(
                {
                    "team": entry["team"],
                    "team_id": entry["team_id"],
                    "blogs": accessible_blogs,
                    "non_rss_count": len(non_rss_blogs),
                }
            )

    sampled = random.sample(eligible, min(n, len(eligible)))
    return sampled
