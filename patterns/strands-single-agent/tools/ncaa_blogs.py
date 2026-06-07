"""Strands tool: look up known fan-blog/forum URLs for an NCAA D1 school.

The agent uses this tool to resolve a school name (e.g. "Duke") into a curated
list of verified fan-forum / blog URLs. Those URLs are then used as context when
performing a web search (e.g. via Tavily), so the agent can ground its answers in
the communities that actually track each program.

The data is a static JSON file (`ncaa_blogs.json`) generated from the dual-verified
fan-forum spreadsheet. It is shipped alongside this module in the container image
(`COPY patterns/strands-single-agent/tools/ tools/` in the Dockerfile), so the JSON
sits next to this file at runtime.
"""

import json
import logging
from pathlib import Path
from typing import Any

from strands import tool

logger = logging.getLogger(__name__)

# The JSON lookup file is shipped next to this module (see module docstring).
_DATA_PATH: Path = Path(__file__).resolve().parent / "ncaa_blogs.json"


def _load_blog_data() -> list[dict[str, Any]]:
    """Load the static NCAA blog lookup data from the JSON file.

    Loaded lazily on first use and cached on the function object so repeated tool
    calls within the same process don't re-read the file from disk.

    Returns:
        A list of school records, each a dict with keys: school, conference,
        mascot, platform, notes, forum_url, forum_healthy, rss_url, rss_healthy.

    Raises:
        FileNotFoundError: If the JSON data file is missing from the image. We fail
            loudly here rather than returning an empty list, so a packaging mistake
            surfaces immediately instead of silently degrading the tool.
    """
    cached: list[dict[str, Any]] | None = getattr(_load_blog_data, "_cache", None)
    if cached is not None:
        return cached

    if not _DATA_PATH.exists():
        raise FileNotFoundError(
            f"NCAA blog data file not found at {_DATA_PATH}. "
            "Ensure ncaa_blogs.json is packaged into the agent container."
        )

    records: list[dict[str, Any]] = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    _load_blog_data._cache = records  # type: ignore[attr-defined]
    return records


def _match_schools(
    *, query: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find school records matching a free-text query.

    Matching is case-insensitive and tiered for predictable behavior:
      1. Exact name match (e.g. "duke" -> "Duke").
      2. Otherwise, substring matches in either direction (e.g. "north carolina"
         matches "North Carolina"; "kansas" matches "Kansas" and "Kansas State").

    Args:
        query: The school name or partial name provided by the user/agent.
        records: The full list of school records to search.

    Returns:
        A list of matching school records (possibly empty).
    """
    normalized: str = query.strip().lower()
    if not normalized:
        return []

    # Tier 1: exact name match.
    exact: list[dict[str, Any]] = [
        r for r in records if r["school"].lower() == normalized
    ]
    if exact:
        return exact

    # Tier 2: bidirectional substring match.
    return [
        r
        for r in records
        if normalized in r["school"].lower() or r["school"].lower() in normalized
    ]


@tool
def lookup_school_blogs(school_name: str) -> str:
    """Look up verified fan-blog and forum URLs for an NCAA Division 1 school.

    Use this BEFORE doing a web search about a specific school's basketball
    program. It returns curated, health-checked community URLs (fan forums and RSS
    feeds) for that school. Feed those URLs into the web search tool as context so
    results are grounded in the communities that track the program. Prefer URLs
    marked as healthy.

    Args:
        school_name: The school name to look up (e.g. "Duke", "Kansas",
            "North Carolina"). Partial names are matched case-insensitively.

    Returns:
        A JSON string with the matched school records. Each record includes the
        school, conference, mascot, platform, notes, forum_url, forum_healthy,
        rss_url, and rss_healthy fields. If no school matches, returns a JSON
        object with a "matches" list of length 0 and a human-readable "message".
    """
    records = _load_blog_data()
    matches = _match_schools(query=school_name, records=records)

    logger.info("[NCAA_BLOGS] query=%r matched %d school(s)", school_name, len(matches))

    if not matches:
        return json.dumps(
            {
                "matches": [],
                "message": (
                    f"No NCAA D1 school matching '{school_name}' was found in the "
                    "blog directory."
                ),
            }
        )

    return json.dumps({"matches": matches})
