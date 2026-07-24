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
from datetime import datetime, timezone
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


def _parse_datetime(entry: dict) -> datetime | None:
    """Extract a full datetime from a feedparser entry.

    Returns a timezone-aware datetime (UTC). If the feed only provides a date
    (no time component), returns midnight UTC for that date.
    """
    for field in ("published_parsed", "updated_parsed"):
        ts = entry.get(field)
        if ts:
            try:
                return datetime(*ts[:6], tzinfo=timezone.utc)
            except (AttributeError, TypeError, ValueError):
                continue

    # Fallback: try to parse the raw date string
    for field in ("published", "updated"):
        raw = entry.get(field, "")
        if raw:
            # Try ISO format first (e.g. 2026-07-21T14:30:00Z)
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                pass
            # Fallback: extract just the date portion
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
            if match:
                return datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3)),
                    tzinfo=timezone.utc,
                )

    return None


def _is_within_window(entry_dt: datetime, dt_start: datetime | None, dt_end: datetime | None) -> bool:
    """Check if an entry datetime falls within the given window.

    For date-only entries (midnight UTC), we treat them as "published sometime
    that day" — they pass if any part of that day overlaps with the window.
    """
    if dt_start and entry_dt < dt_start:
        # Special case: if entry has no time component (midnight), include if
        # the entry's DATE is >= the start DATE (it may have been published later that day)
        if entry_dt.hour == 0 and entry_dt.minute == 0 and entry_dt.second == 0:
            return entry_dt.date() >= dt_start.date()
        return False
    if dt_end and entry_dt > dt_end:
        return False
    return True


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
    datetime_start: str = "",
    datetime_end: str = "",
    event_types: str = "",
    sport: str = "NCAA Men's Basketball",
) -> dict[str, Any]:
    """Fetch an RSS/Atom feed and extract events using LLM.

    Parses the feed, filters entries to only those within the datetime window,
    then uses a lightweight model to detect sports events from the filtered entries.

    Args:
        rss_url: The full URL of the RSS or Atom feed.
        team: Team name to filter events for (e.g. "Colgate Raiders").
        datetime_start: Start of datetime window (ISO format, e.g. "2026-07-24T13:00:00+00:00"
            or "YYYY-MM-DD" for date-only). Entries before this are excluded.
        datetime_end: End of datetime window (ISO format or YYYY-MM-DD).
            Entries after this are excluded.
        event_types: Comma-separated event types to detect (e.g. "INJURY,ROSTER").
        sport: Sport scope (default: "NCAA Men's Basketball").

    Returns:
        A dict with keys:
          - source: the feed URL fetched
          - entries_total: total entries in feed
          - entries_in_window: entries that passed the datetime filter
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
                "entries_total": 0,
                "entries_in_window": 0,
                "events": [],
                "extraction_error": None,
                "error": error_msg,
            }

        if feed.bozo and not feed.entries:
            error_msg = str(feed.bozo_exception) if feed.bozo_exception else "Feed parse error"
            logger.warning("[RSS_FETCH] url=%s bozo_error=%s", rss_url, error_msg)
            return {
                "source": rss_url,
                "entries_total": 0,
                "entries_in_window": 0,
                "events": [],
                "extraction_error": None,
                "error": error_msg,
            }

        # Parse the datetime window boundaries
        dt_start = None
        dt_end = None
        if datetime_start:
            try:
                dt_start = datetime.fromisoformat(datetime_start.replace("Z", "+00:00"))
                if dt_start.tzinfo is None:
                    dt_start = dt_start.replace(tzinfo=timezone.utc)
            except ValueError:
                # Fallback: treat as YYYY-MM-DD
                match = re.search(r"(\d{4})-(\d{2})-(\d{2})", datetime_start)
                if match:
                    dt_start = datetime(
                        int(match.group(1)), int(match.group(2)), int(match.group(3)),
                        tzinfo=timezone.utc,
                    )
        if datetime_end:
            try:
                dt_end = datetime.fromisoformat(datetime_end.replace("Z", "+00:00"))
                if dt_end.tzinfo is None:
                    dt_end = dt_end.replace(tzinfo=timezone.utc)
            except ValueError:
                match = re.search(r"(\d{4})-(\d{2})-(\d{2})", datetime_end)
                if match:
                    # End of day for date-only end bounds
                    dt_end = datetime(
                        int(match.group(1)), int(match.group(2)), int(match.group(3)),
                        23, 59, 59, tzinfo=timezone.utc,
                    )

        # Parse all entries and filter by datetime window
        all_entries = []
        for entry in feed.entries[:_MAX_ENTRIES]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            entry_dt = _parse_datetime(entry)

            description = ""
            if entry.get("summary"):
                description = _strip_html(entry.summary)[:1000]
            elif entry.get("content"):
                for c in entry.content:
                    if c.get("value"):
                        description = _strip_html(c["value"])[:1000]
                        break

            all_entries.append({
                "title": title,
                "link": link,
                "datetime": entry_dt,
                "description": description,
            })

        # Apply datetime window filter
        if dt_start or dt_end:
            filtered_entries = [
                e for e in all_entries
                if e["datetime"] is not None and _is_within_window(e["datetime"], dt_start, dt_end)
            ]
            # Also include entries with no timestamp (can't determine — let LLM decide)
            no_timestamp_entries = [e for e in all_entries if e["datetime"] is None]
            filtered_entries.extend(no_timestamp_entries)
        else:
            filtered_entries = all_entries

        logger.info(
            "[RSS_FETCH] url=%s total_entries=%d in_window=%d window=%s→%s",
            rss_url, len(all_entries), len(filtered_entries),
            dt_start.isoformat() if dt_start else "any",
            dt_end.isoformat() if dt_end else "any",
        )

        # If no entries in window, skip LLM call entirely
        if not filtered_entries:
            return {
                "source": rss_url,
                "entries_total": len(all_entries),
                "entries_in_window": 0,
                "events": [],
                "extraction_error": None,
                "error": None,
            }

        # If no team provided, skip LLM extraction (backward compat)
        if not team:
            return {
                "source": rss_url,
                "entries_total": len(all_entries),
                "entries_in_window": len(filtered_entries),
                "events": [],
                "extraction_error": "No team provided, skipping extraction",
                "error": None,
            }

        # Format filtered entries as text for LLM extraction
        lines = []
        for i, e in enumerate(filtered_entries, 1):
            dt_str = e["datetime"].isoformat() if e["datetime"] else "unknown"
            lines.append(f"Entry {i}:")
            lines.append(f"  Title: {e['title']}")
            lines.append(f"  Link: {e['link']}")
            lines.append(f"  Published: {dt_str}")
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
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            model_id=RSS_EXTRACTION_MODEL_ID,
        )

        return {
            "source": rss_url,
            "entries_total": len(all_entries),
            "entries_in_window": len(filtered_entries),
            "events": result["events"],
            "extraction_error": result["extraction_error"],
            "error": None,
        }

    except Exception as e:
        logger.warning("[RSS_FETCH] url=%s error=%s", rss_url, str(e))
        return {
            "source": rss_url,
            "entries_total": 0,
            "entries_in_window": 0,
            "events": [],
            "extraction_error": None,
            "error": str(e),
        }
