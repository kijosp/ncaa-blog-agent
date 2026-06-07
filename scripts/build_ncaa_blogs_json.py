#!/usr/bin/env python3
"""One-off generator: convert the verified fan-forum CSV into the agent's lookup JSON.

Reads the dual-verified fan forum CSV and emits a compact JSON file the agent tool
loads at runtime to map a school name -> its known-good blog/forum URLs.

Input columns (CSV):
    Conference, School Name, Mascot, Forum URL, Forum Status, RSS Feed URL,
    RSS Status, Platform Type, Pipeline Insights & Notes, JSON API URL

Output (JSON): a list of school records. A URL is considered "healthy" when its
status string contains "ACTIVE". Unhealthy URLs are still recorded but flagged so
the tool can prefer healthy ones without silently dropping data.
"""

import csv
import json
from pathlib import Path
from typing import Any

# Resolve paths relative to the repository root (this script lives in scripts/).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
CSV_PATH: Path = REPO_ROOT / "data" / "cbb_fan_forums_DUAL_VERIFIED_v2.csv"
JSON_OUT: Path = (
    REPO_ROOT / "patterns" / "strands-single-agent" / "tools" / "ncaa_blogs.json"
)


def _is_healthy(status: str) -> bool:
    """Return whether a verification status string indicates a reachable URL.

    Args:
        status: The raw status string from the CSV (e.g. "ACTIVE (200 OK)").

    Returns:
        True when the status indicates an active/reachable resource, else False.
    """
    return "ACTIVE" in (status or "").upper()


def build_records() -> list[dict[str, Any]]:
    """Parse the CSV into a list of normalized school blog records.

    Returns:
        A list of dicts, one per school, each containing the school name,
        conference, mascot, platform, notes, and the forum/RSS URLs with their
        health flags.

    Raises:
        FileNotFoundError: If the source CSV does not exist (fail loudly rather
            than emitting an empty file).
    """
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Source CSV not found: {CSV_PATH}")

    records: list[dict[str, Any]] = []
    # utf-8-sig strips a leading BOM if present so the first header isn't mangled.
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            forum_url: str = (row.get("Forum URL") or "").strip()
            rss_url: str = (row.get("RSS Feed URL") or "").strip()

            records.append(
                {
                    "school": (row.get("School Name") or "").strip(),
                    "conference": (row.get("Conference") or "").strip(),
                    "mascot": (row.get("Mascot") or "").strip(),
                    "platform": (row.get("Platform Type") or "").strip(),
                    "notes": (row.get("Pipeline Insights & Notes") or "").strip(),
                    "forum_url": forum_url,
                    "forum_healthy": _is_healthy(row.get("Forum Status") or ""),
                    "rss_url": rss_url,
                    "rss_healthy": _is_healthy(row.get("RSS Status") or ""),
                }
            )
    return records


def main() -> None:
    """Generate the JSON lookup file and print a short summary to stdout."""
    records = build_records()
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False))

    healthy_forums = sum(1 for r in records if r["forum_healthy"])
    print(f"Wrote {len(records)} schools to {JSON_OUT}")
    print(f"Healthy forum URLs: {healthy_forums}")


if __name__ == "__main__":
    main()
