"""Tool: fetch webpage content and extract readable text.

Enhanced version of discovery's http_fetch tool. Adds:
  - Date extraction from page content
  - Content section extraction (finds the main article body)
"""

import logging
import re
from typing import Any

import requests
from strands import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 15
_MAX_CONTENT_BYTES: int = 500_000  # 500KB max download
_MAX_RETURN_CHARS: int = 15_000  # 15K chars returned to agent (context-friendly)


def _html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace into readable text.

    Removes script/style blocks, then strips all remaining tags.

    Args:
        html: Raw HTML content.

    Returns:
        Clean text with tags removed and whitespace normalized.
    """
    # Remove script and style blocks entirely
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove head block
    text = re.sub(r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove nav/header/footer blocks (usually not article content)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert block elements to newlines for readability
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace but preserve paragraph breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def _extract_dates(text: str) -> list[str]:
    """Extract date strings from text content.

    Returns:
        List of dates found in YYYY-MM-DD format, most recent first.
    """
    dates = set()

    # ISO format: 2026-07-01
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2020 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
            dates.add(m.group(0))

    # Written format: July 1, 2026 / Jul 1, 2026
    months_map = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09",
        "oct": "10", "nov": "11", "dec": "12",
    }
    for m in re.finditer(r"\b([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})\b", text):
        month_name = m.group(1).lower()
        if month_name in months_map:
            day = int(m.group(2))
            year = int(m.group(3))
            if 2020 <= year <= 2030 and 1 <= day <= 31:
                dates.add(f"{year}-{months_map[month_name]}-{day:02d}")

    return sorted(dates, reverse=True)


@tool
def web_fetch(url: str) -> dict[str, Any]:
    """Fetch a webpage and extract readable text content.

    Fetches the URL, strips HTML, and returns clean text plus metadata
    about what dates appear on the page.

    Args:
        url: The full URL to fetch (e.g. "https://colgatefanforum.com/news/post-123").

    Returns:
        A dict with keys:
          - url: the URL fetched
          - content: clean text content (truncated to 15K chars for context efficiency)
          - content_length: character count of the full extracted text
          - dates_found: list of dates found on page (YYYY-MM-DD, most recent first)
          - error: str or None
    """
    try:
        resp = requests.get(
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
        resp.raise_for_status()

        raw = resp.content[:_MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
        text = _html_to_text(raw)
        dates = _extract_dates(text)

        content_for_return = text[:_MAX_RETURN_CHARS]

        logger.info(
            "[WEB_FETCH] url=%s content_length=%d dates=%d",
            url, len(text), len(dates),
        )

        return {
            "url": url,
            "content": content_for_return,
            "content_length": len(text),
            "dates_found": dates[:10],
            "error": None,
        }

    except requests.RequestException as e:
        logger.warning("[WEB_FETCH] url=%s error=%s", url, str(e))
        return {
            "url": url,
            "content": "",
            "content_length": 0,
            "dates_found": [],
            "error": str(e),
        }
