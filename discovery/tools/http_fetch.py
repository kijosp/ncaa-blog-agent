"""Tool: fetch full page content from a URL and return clean text.

Strips HTML boilerplate and returns readable text content.
"""

import logging
import re
from typing import Any

import requests
from strands import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 15
_MAX_CONTENT_BYTES: int = 500_000  # 500KB max


def _html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace into readable text.

    Args:
        html: Raw HTML content.

    Returns:
        Clean text with tags removed and whitespace normalized.
    """
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


@tool
def http_fetch(url: str) -> dict[str, Any]:
    """Fetch a webpage and return its text content with HTML stripped.

    Use this to read the full content of a webpage after finding its URL
    via web_search. Returns clean text (no HTML tags).

    Args:
        url: The full URL to fetch (e.g. "https://en.wikipedia.org/wiki/...").

    Returns:
        A dict with keys:
          - url: the URL fetched
          - content: clean text content of the page (truncated to ~100k chars)
          - content_length: character count of the returned content
          - error: str or None
    """
    try:
        resp = requests.get(
            url,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BlogDiscoveryBot/1.0)"},
        )
        resp.raise_for_status()
        raw = resp.content[:_MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
        text = _html_to_text(raw)
        # Truncate to ~100k chars to fit in context
        text = text[:100_000]
        logger.info("[HTTP_FETCH] url=%s content_length=%d", url, len(text))
        return {
            "url": url,
            "content": text,
            "content_length": len(text),
            "error": None,
        }
    except requests.RequestException as e:
        logger.warning("[HTTP_FETCH] url=%s error=%s", url, str(e))
        return {
            "url": url,
            "content": "",
            "content_length": 0,
            "error": str(e),
        }
