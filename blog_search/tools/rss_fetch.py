"""Tool: fetch and parse RSS/Atom feeds, then extract events via LLM.

Uses feedparser for robust handling of all feed formats:
  - RSS 1.0 (RDF), RSS 2.0, Atom
  - Handles malformed XML, CDATA, namespace quirks
  - JSON Feed support

After parsing, calls llm_extractor to detect events using a lightweight model.
"""

import asyncio
import logging
import re
from typing import Any

import feedparser
from strands import tool

from tools.llm_extractor import extract_events, RSS_EXTRACTION_MODEL_ID

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 15
_MAX_ENTRIES: int = 20


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(entry: dict) -> str | None:
    """Extract a YYYY-MM-DD date from a feedparser entry."""
    for field in ("published_parsed", "updated_parsed"):
        ts = entry.get(field)
        if ts:
            try:
                return f"{ts.tm_year:04d}-{ts.tm_mon:02d}-{ts.tm_mday:02d}"
            except (AttributeError, TypeError):
                continue

    for field in ("published", "updated"):
        raw = entry.get(field, "")
        if raw:
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
            if match:
                return match.group(0)

    return None


def _parse_feed(rss_url: str) -> dict:
    """Synchronous feed parsing (runs in thread)."""
    return feedparser.parse(
        rss_url,
        agent="Mozilla/5.0 (compatible; BlogSearchBot/1.0)",
    )


@tool
async def rss_fetch(
    rss_url: str,
    team: str = "",
    date_start: str = "",
    date_end: str = "",
    event_types: str = "",
    sport: str = "NCAA Men's Basketball",
) -> dict[str, Any]:
    """Fetch an RSS/Atom feed and extract events using LLM.

    Parses the feed, then uses a lightweight model to detect sports events
    matching the specified types and date range.

    Args:
        rss_url: The full URL of the RSS or Atom feed.
        team: Team name to filter events for (e.g. "Colgate Raiders").
        date_start: Start of date range (YYYY-MM-DD). Events before this are excluded.
        date_end: End of date range (YYYY-MM-DD). Events after this are excluded.
        event_types: Comma-separated event types to detect (e.g. "INJURY,ROSTER").
        sport: Sport scope (default: "NCAA Men's Basketball").

    Returns:
        A dict with keys:
          - source: the feed URL fetched
          - events: list of extracted event dicts
          - extraction_error: str or None
          - error: str or None (feed-level errors)
    """
    try:
        feed = await asyncio.to_thread(_parse_feed, rss_url)

        if hasattr(feed, "status") and feed.status and feed.status >= 400:
            error_msg = f"HTTP {feed.status}"
            logger.warning("[RSS_FETCH] url=%s error=%s", rss_url, error_msg)
            return {
                "source": rss_url,
                "events": [],
                "extraction_error": None,
                "error": error_msg,
            }

        if feed.bozo and not feed.entries:
            error_msg = str(feed.bozo_exception) if feed.bozo_exception else "Feed parse error"
            logger.warning("[RSS_FETCH] url=%s bozo_error=%s", rss_url, error_msg)
            return {
                "source": rss_url,
                "events": [],
                "extraction_error": None,
                "error": error_msg,
            }

        entries = []
        for entry in feed.entries[:_MAX_ENTRIES]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            date = _parse_date(entry)

            description = ""
            if entry.get("summary"):
                description = _strip_html(entry.summary)[:1000]
            elif entry.get("content"):
                for c in entry.content:
                    if c.get("value"):
                        description = _strip_html(c["value"])[:1000]
                        break

            entries.append({
                "title": title,
                "link": link,
                "date": date,
                "description": description,
            })

        most_recent = None
        for e in entries:
            if e.get("date"):
                if most_recent is None or e["date"] > most_recent:
                    most_recent = e["date"]

        logger.info(
            "[RSS_FETCH] url=%s entries=%d most_recent=%s",
            rss_url, len(entries), most_recent,
        )

        # If no team provided, skip LLM extraction (backward compat)
        if not team:
            return {
                "source": rss_url,
                "events": [],
                "extraction_error": "No team provided, skipping extraction",
                "error": None,
            }

        # Format entries as text for LLM extraction
        lines = []
        for i, e in enumerate(entries, 1):
            lines.append(f"Entry {i}:")
            lines.append(f"  Title: {e['title']}")
            lines.append(f"  Link: {e['link']}")
            lines.append(f"  Date: {e['date'] or 'unknown'}")
            lines.append(f"  Description: {e['description']}")
            lines.append("")
        formatted_text = "\n".join(lines)

        # Parse event_types string to list
        types_list = [t.strip() for t in event_types.split(",") if t.strip()] if event_types else []

        result = await extract_events(
            content=formatted_text,
            source_url=rss_url,
            retrieval_method="rss",
            team=team,
            sport=sport,
            event_types=types_list,
            date_start=date_start,
            date_end=date_end,
            model_id=RSS_EXTRACTION_MODEL_ID,
        )

        return {
            "source": rss_url,
            "events": result["events"],
            "extraction_error": result["extraction_error"],
            "error": None,
        }

    except Exception as e:
        logger.warning("[RSS_FETCH] url=%s error=%s", rss_url, str(e))
        return {
            "source": rss_url,
            "events": [],
            "extraction_error": None,
            "error": str(e),
        }
