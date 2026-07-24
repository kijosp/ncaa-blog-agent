"""Tool: fetch webpage content and extract events via LLM.

Fetches a URL, strips HTML to text, then calls llm_extractor to detect
structured events from the page content.
"""

import asyncio
import logging
import re
from typing import Any

import requests
from strands import tool

from tools.llm_extractor import extract_events

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 15
_MAX_CONTENT_BYTES: int = 500_000  # 500KB max download
_MAX_TEXT_FOR_EXTRACTION: int = 30_000  # 30K chars passed to LLM


def _html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace into readable text."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def _fetch_url(url: str) -> requests.Response:
    """Synchronous HTTP GET (runs in thread)."""
    return requests.get(
        url,
        timeout=_TIMEOUT_SECONDS,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        allow_redirects=True,
    )


@tool
async def web_fetch(
    url: str,
    team: str = "",
    datetime_start: str = "",
    datetime_end: str = "",
    event_types: str = "",
    sport: str = "NCAA Men's Basketball",
    search_context: str = "",
) -> dict[str, Any]:
    """Fetch a webpage and extract events using LLM.

    Fetches the URL, strips HTML to text, then uses a lightweight model to
    detect sports events matching the specified types and date range.

    Args:
        url: The full URL to fetch (e.g. "https://colgatefanforum.com/news/post-123").
        team: Team name to filter events for (e.g. "Colgate Raiders").
        datetime_start: Start of datetime window (ISO format, e.g. "2026-07-24T13:00:00+00:00"
            or "YYYY-MM-DD"). Events before this are excluded.
        datetime_end: End of datetime window (ISO format or YYYY-MM-DD).
            Events after this are excluded.
        event_types: Comma-separated event types to detect (e.g. "INJURY,ROSTER").
        sport: Sport scope (default: "NCAA Men's Basketball").
        search_context: If set, indicates this fetch follows a web search. Sets retrieval_method to "web_search + web_fetch".

    Returns:
        A dict with keys:
          - source: the URL fetched
          - events: list of extracted event dicts
          - extraction_error: str or None
          - error: str or None (HTTP errors)
    """
    try:
        resp = await asyncio.to_thread(_fetch_url, url)
        resp.raise_for_status()

        raw = resp.content[:_MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
        text = _html_to_text(raw)

        logger.info("[WEB_FETCH] url=%s content_length=%d", url, len(text))

        # If no team provided, skip LLM extraction (backward compat)
        if not team:
            return {
                "source": url,
                "events": [],
                "extraction_error": "No team provided, skipping extraction",
                "error": None,
            }

        retrieval_method = "web_search + web_fetch" if search_context else "web_fetch"

        types_list = [t.strip() for t in event_types.split(",") if t.strip()] if event_types else []

        result = await extract_events(
            content=text[:_MAX_TEXT_FOR_EXTRACTION],
            source_url=url,
            retrieval_method=retrieval_method,
            team=team,
            sport=sport,
            event_types=types_list,
            datetime_start=datetime_start,
            datetime_end=datetime_end,
        )

        return {
            "source": url,
            "events": result["events"],
            "extraction_error": result["extraction_error"],
            "error": None,
        }

    except requests.RequestException as e:
        logger.warning("[WEB_FETCH] url=%s error=%s", url, str(e))
        return {
            "source": url,
            "events": [],
            "extraction_error": None,
            "error": str(e),
        }
