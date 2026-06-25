#!/usr/bin/env python3
"""Summarize the blog discovery registry JSON output.

Usage:
    python discovery/summarize.py discovery/output/ncaa_mbb_registry.json
"""

import json
import sys
from collections import Counter
from pathlib import Path


def summarize(registry_path: str) -> None:
    """Print a summary of the discovery registry.

    Args:
        registry_path: Path to the registry JSON file.
    """
    data = json.loads(Path(registry_path).read_text())

    total_teams = len(data)
    teams_with_accessible = 0
    teams_without_accessible = 0
    error_counter: Counter = Counter()
    teams_no_blogs: list[str] = []

    for team_name, entry in data.items():
        blogs = entry.get("blogs", [])
        has_accessible = any(b.get("accessible") for b in blogs)

        if has_accessible:
            teams_with_accessible += 1
        else:
            teams_without_accessible += 1
            # Count error types for inaccessible blogs
            for b in blogs:
                label = b.get("status_label", "no_blogs_found")
                error_counter[label] += 1
            if not blogs:
                error_counter["no_blogs_found"] += 1
                teams_no_blogs.append(team_name)

    print(f"\n{'='*50}")
    print(f"  DISCOVERY SUMMARY: {registry_path}")
    print(f"{'='*50}")
    print(f"  Total teams:                 {total_teams}")
    print(f"  Teams with ≥1 accessible URL: {teams_with_accessible} ({teams_with_accessible/total_teams*100:.0f}%)")
    print(f"  Teams with NO accessible URL: {teams_without_accessible} ({teams_without_accessible/total_teams*100:.0f}%)")

    if error_counter:
        print(f"\n  Top access errors (teams without accessible URLs):")
        for error, count in error_counter.most_common(10):
            pct = count / sum(error_counter.values()) * 100
            print(f"    {error:20s}  {count:4d}  ({pct:.0f}%)")

    if teams_no_blogs and len(teams_no_blogs) <= 20:
        print(f"\n  Teams with zero blogs found:")
        for t in teams_no_blogs:
            print(f"    - {t}")

    print(f"{'='*50}\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <registry.json>")
        sys.exit(1)
    summarize(sys.argv[1])
