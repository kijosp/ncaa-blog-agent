"""Tool: check recency of a blog/forum by extracting the latest post date.

Fetches the page HTML and heuristically extracts dates to determine
when the most recent content was posted.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from strands import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 15
_MAX_CONTENT_BYTES: int = 200_000  # Only read first 200KB for date scanning

# Common date patterns found on forums and blogs.
_DATE_PATTERNS: list[tuple[str, str]] = [
    # 2026-06-23, 2026/06/23
    (r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b", "%Y-%m-%d"),
    # Jun 23, 2026 / June 23, 2026
    (r"\b([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})\b", "%B %d, %Y"),
    # 23 Jun 2026
    (r"\b(\d{1,2}\s+[A-Z][a-z]{2,8}\s+\d{4})\b", "%d %B %Y"),
    # 06/23/2026, 06-23-2026
    (r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b", "%m-%d-%Y"),
]


def _llm_extract_date(text: str) -> datetime | None:
    """Use Bedrock LLM to extract the most recent post date from page text.

    Called only when regex-based extraction finds no dates. Keeps cost low
    by only sending the first ~2KB of content.

    Args:
        text: First ~2000 chars of the page content (already stripped of HTML).

    Returns:
        The most recent post date as a datetime, or None if LLM can't find one.
    """
    import boto3
    import json as json_mod
    import os

    try:
        session = boto3.Session(
            profile_name=os.environ.get("AWS_PROFILE"),
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
        client = session.client("bedrock-runtime")
        resp = client.converse(
            modelId="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            messages=[{
                "role": "user",
                "content": [{"text": (
                    "What is the most recent post or article date on this webpage? "
                    "Look for dates associated with posts, articles, or threads — not copyright years or join dates. "
                    "Respond with ONLY the date in YYYY-MM-DD format. If no post date is found, respond with NONE.\n\n"
                    f"Page content:\n{text}"
                )}],
            }],
            inferenceConfig={"maxTokens": 20, "temperature": 0.0},
        )
        answer = resp["output"]["message"]["content"][0]["text"].strip()
        logger.debug("[RECENCY_CHECK] LLM fallback returned: %s", answer)
        if answer == "NONE" or len(answer) != 10:
            return None
        dt = datetime.strptime(answer, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= now:
            return dt
        return None
    except Exception as e:
        logger.debug("[RECENCY_CHECK] LLM fallback error: %s", e)
        return None


def _extract_latest_date(html: str) -> datetime | None:
    """Parse HTML text for date patterns and return the most recent one.

    Args:
        html: Raw HTML content to scan for dates.

    Returns:
        The most recent datetime found, or None if no dates detected.
    """
    dates: list[datetime] = []
    now = datetime.now(tz=timezone.utc)

    for pattern, fmt in _DATE_PATTERNS:
        matches = re.findall(pattern, html)
        if matches:
            logger.debug("[RECENCY_CHECK] pattern=%r found %d matches: %s", pattern, len(matches), matches[:5])
        for match in matches:
            normalized = match.replace("/", "-").replace(",", "")
            try:
                dt = datetime.strptime(normalized, fmt.replace("/", "-").replace(",", ""))
                dt = dt.replace(tzinfo=timezone.utc)
                # Discard dates in the future or before 2000
                if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= now:
                    dates.append(dt)
            except ValueError:
                continue

    if dates:
        logger.debug("[RECENCY_CHECK] all valid dates found (%d total), latest=%s, oldest=%s",
                     len(dates), max(dates).strftime("%Y-%m-%d"), min(dates).strftime("%Y-%m-%d"))
    else:
        logger.debug("[RECENCY_CHECK] no valid dates extracted from page content")

    return max(dates) if dates else None


@tool
def recency_check(url: str) -> dict[str, Any]:
    """Check how recently a blog or forum was updated by scanning for dates in its HTML.

    Fetches the page and extracts date patterns to find the most recent post date.
    Returns whether the site is "active" (posted within threshold) or "outdated".

    Args:
        url: The full URL of the blog/forum to check.

    Returns:
        A dict with keys:
          - url: the URL checked
          - last_post_date: ISO-8601 date string of the most recent post (or null)
          - days_since_last_post: int days since the last post (or null)
          - recency_status: "active" if within 365 days, "outdated" otherwise, "unknown" if no dates found
          - error: str or None
    """
    try:
        resp = requests.get(
            url,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BlogDiscoveryBot/1.0)"},
            stream=True,
        )
        resp.raise_for_status()
        content = resp.content[:_MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
        logger.debug("[RECENCY_CHECK] url=%s | fetched %d bytes | status=%d", url, len(content), resp.status_code)
    except requests.RequestException as e:
        logger.warning("[RECENCY_CHECK] url=%s error=%s", url, str(e))
        return {
            "url": url,
            "last_post_date": None,
            "days_since_last_post": None,
            "recency_status": "unknown",
            "error": str(e),
        }

    latest = _extract_latest_date(content)
    if latest is None:
        logger.info("[RECENCY_CHECK] url=%s no dates via regex, trying LLM fallback", url)
        latest = _llm_extract_date(content[:2000])

    if latest is None:
        logger.info("[RECENCY_CHECK] url=%s no dates found (regex + LLM)", url)
        return {
            "url": url,
            "last_post_date": None,
            "days_since_last_post": None,
            "recency_status": "unknown",
            "error": None,
        }

    days_since = (datetime.now(tz=timezone.utc) - latest).days
    status = "active" if days_since <= 365 else "outdated"
    logger.info("[RECENCY_CHECK] url=%s last_post=%s days=%d status=%s", url, latest.date(), days_since, status)

    return {
        "url": url,
        "last_post_date": latest.strftime("%Y-%m-%d"),
        "days_since_last_post": days_since,
        "recency_status": status,
        "error": None,
    }
