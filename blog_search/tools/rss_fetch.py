"""Tool: fetch and parse RSS/Atom feeds for blog content.

Uses feedparser for robust handling of all feed formats:
  - RSS 1.0 (RDF), RSS 2.0, Atom
  - Handles malformed XML, CDATA, namespace quirks
  - JSON Feed support

Returns all entries unfiltered — the agent decides what's relevant.
"""

import logging
import re
from typing import Any

import feedparser
from strands import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 15
_MAX_ENTRIES: int = 20  # Return at most this many recent entries


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(entry: dict) -> str | None:
    """Extract a YYYY-MM-DD date from a feedparser entry.

    Feedparser normalizes dates into a time_struct in published_parsed or updated_parsed.
    """
    for field in ("published_parsed", "updated_parsed"):
        ts = entry.get(field)
        if ts:
            try:
                return f"{ts.tm_year:04d}-{ts.tm_mon:02d}-{ts.tm_mday:02d}"
            except (AttributeError, TypeError):
                continue

    # Fallback: try to extract date from the raw string
    for field in ("published", "updated"):
        raw = entry.get(field, "")
        if raw:
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
            if match:
                return match.group(0)

    return None


@tool
def rss_fetch(rss_url: str) -> dict[str, Any]:
    """Fetch an RSS/Atom feed and return all entries with clean parsed content.

    Handles RSS 1.0, RSS 2.0, Atom, and even malformed feeds.
    Returns title, link, date, and description for each entry — no filtering applied.

    Args:
        rss_url: The full URL of the RSS or Atom feed.

    Returns:
        A dict with keys:
          - rss_url: the feed URL fetched
          - entries: list of entry dicts (title, link, date, description)
          - total_entries: number of entries returned
          - most_recent_date: date of the newest entry (YYYY-MM-DD or null)
          - feed_title: title of the feed itself (e.g., "The Colgate Maroon-News")
          - error: str or None
    """
    try:
        feed = feedparser.parse(
            rss_url,
            agent="Mozilla/5.0 (compatible; BlogSearchBot/1.0)",
        )

        # Check for HTTP errors
        if hasattr(feed, "status") and feed.status and feed.status >= 400:
            error_msg = f"HTTP {feed.status}"
            logger.warning("[RSS_FETCH] url=%s error=%s", rss_url, error_msg)
            return {
                "rss_url": rss_url,
                "entries": [],
                "total_entries": 0,
                "most_recent_date": None,
                "feed_title": None,
                "error": error_msg,
            }

        # Check for parse errors (bozo flag)
        if feed.bozo and not feed.entries:
            error_msg = str(feed.bozo_exception) if feed.bozo_exception else "Feed parse error"
            logger.warning("[RSS_FETCH] url=%s bozo_error=%s", rss_url, error_msg)
            return {
                "rss_url": rss_url,
                "entries": [],
                "total_entries": 0,
                "most_recent_date": None,
                "feed_title": None,
                "error": error_msg,
            }

        feed_title = feed.feed.get("title", "").strip() if feed.feed else None

        entries = []
        for entry in feed.entries[:_MAX_ENTRIES]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            date = _parse_date(entry)

            # Get description: prefer summary, fall back to content
            description = ""
            if entry.get("summary"):
                description = _strip_html(entry.summary)[:1000]
            elif entry.get("content"):
                # content is a list of dicts with 'value' key
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

        # Find most recent date
        most_recent = None
        for e in entries:
            if e.get("date"):
                if most_recent is None or e["date"] > most_recent:
                    most_recent = e["date"]

        logger.info(
            "[RSS_FETCH] url=%s feed_title=%s entries=%d most_recent=%s",
            rss_url, feed_title, len(entries), most_recent,
        )

        return {
            "rss_url": rss_url,
            "entries": entries,
            "total_entries": len(entries),
            "most_recent_date": most_recent,
            "feed_title": feed_title,
            "error": None,
        }

    except Exception as e:
        logger.warning("[RSS_FETCH] url=%s error=%s", rss_url, str(e))
        return {
            "rss_url": rss_url,
            "entries": [],
            "total_entries": 0,
            "most_recent_date": None,
            "feed_title": None,
            "error": str(e),
        }
