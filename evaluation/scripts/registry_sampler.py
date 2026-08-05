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
    n: int = 2,
    seed: int = 42,
    min_blogs: int = 1,
    registry_path: Path | None = None,
) -> list[dict]:
    """Select n random teams that have at least min_blogs accessible blogs.

    Returns list of dicts with 'team', 'team_id', and 'blogs' (filtered to accessible only).
    """
    random.seed(seed)
    registry = load_registry(registry_path)

    eligible = []
    for entry in registry:
        accessible_blogs = [b for b in entry.get("blogs", []) if b.get("accessible")]
        if len(accessible_blogs) >= min_blogs:
            eligible.append(
                {
                    "team": entry["team"],
                    "team_id": entry["team_id"],
                    "blogs": accessible_blogs,
                }
            )

    sampled = random.sample(eligible, min(n, len(eligible)))
    return sampled
