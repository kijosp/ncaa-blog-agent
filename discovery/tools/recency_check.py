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
_MAX_CONTENT_BYTES: int = 500_000  # Read first 500KB for date scanning

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


def _llm_extract_date(url: str, visible_html: str) -> datetime | None:
    """Use Bedrock LLM to extract the most recent post date from page content.

    Called only when regex-based extraction finds no dates. Receives
    visible HTML (already stripped of head/script/style via _strip_non_visible_html),
    then converts to plain text for the LLM.

    Args:
        url: The URL being checked (for context).
        visible_html: The visible page content (output of _strip_non_visible_html).

    Returns:
        The most recent post date as a datetime, or None if LLM can't find one.
    """
    import boto3
    import json as json_mod
    import os

    # Convert remaining HTML tags to spaces to get plain readable text
    plain_text = re.sub(r'<[^>]+>', ' ', visible_html)
    plain_text = re.sub(r'\s+', ' ', plain_text).strip()
    plain_text = plain_text[:50_000]

    if not plain_text or len(plain_text) < 20:
        return None

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
                    f"URL: {url}\n\n"
                    "What is the most recent post or article date on this webpage? "
                    "Look for dates associated with posts, articles, news stories, or forum threads. "
                    "Ignore copyright years, user join dates, or site-wide footers. "
                    "Respond with ONLY the date in YYYY-MM-DD format. If no post date is found, respond with NONE.\n\n"
                    f"Page content:\n{plain_text}"
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


def _extract_date_from_rss(rss_url: str) -> datetime | None:
    """Try to get the most recent date from an RSS feed.

    Args:
        rss_url: URL of the RSS feed.

    Returns:
        The most recent item date as a datetime, or None if unavailable.
    """
    if not rss_url:
        return None
    try:
        resp = requests.get(
            rss_url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BlogDiscoveryBot/1.0)"},
        )
        resp.raise_for_status()
        content = resp.text[:100_000]

        # Look for pubDate or dc:date in RSS items
        # pubDate format: "Mon, 16 Jun 2026 00:00:00 GMT" or similar
        pub_dates = re.findall(r'<pubDate>([^<]+)</pubDate>', content)
        dc_dates = re.findall(r'<dc:date>([^<]+)</dc:date>', content)

        # Also look for dates in <link> or <guid> URLs like /news/2026/6/16/...
        url_dates = re.findall(r'/(?:news|stories|archives)/(\d{4})/(\d{1,2})/(\d{1,2})/', content)

        dates: list[datetime] = []
        now = datetime.now(tz=timezone.utc)

        # Parse pubDate strings
        from email.utils import parsedate_to_datetime
        for pd in pub_dates:
            try:
                dt = parsedate_to_datetime(pd)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= now:
                    dates.append(dt)
            except (ValueError, TypeError):
                continue

        # Parse dc:date strings (ISO format)
        for dd in dc_dates:
            try:
                dt = datetime.fromisoformat(dd.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= now:
                    dates.append(dt)
            except (ValueError, TypeError):
                continue

        # Parse URL-embedded dates
        for year, month, day in url_dates:
            try:
                dt = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
                if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= now:
                    dates.append(dt)
            except (ValueError, TypeError):
                continue

        if dates:
            latest = max(dates)
            logger.info("[RECENCY_CHECK] RSS date found: %s from %s", latest.strftime("%Y-%m-%d"), rss_url)
            return latest

    except Exception as e:
        logger.debug("[RECENCY_CHECK] RSS fetch failed for %s: %s", rss_url, e)

    return None


def _strip_non_visible_html(html: str) -> str:
    """Remove HTML elements that are not visible page content.

    Strips <head>, <script>, <style>, <noscript> blocks and HTML comments.
    Keeps the rendered body content including post dates, comment dates,
    star ratings, and other visible elements.

    Args:
        html: Raw HTML string.

    Returns:
        HTML with invisible/metadata elements removed.
    """
    # Remove everything in <head>...</head>
    cleaned = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove <script>...</script> blocks
    cleaned = re.sub(r'<script[^>]*>.*?</script>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Remove <style>...</style> blocks
    cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Remove <noscript>...</noscript> blocks
    cleaned = re.sub(r'<noscript[^>]*>.*?</noscript>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.DOTALL)
    # Remove <meta> tags (self-closing, can contain stale dates)
    cleaned = re.sub(r'<meta[^>]*/?>', '', cleaned, flags=re.IGNORECASE)
    # Remove <link> tags (often contain dates in href attributes)
    cleaned = re.sub(r'<link[^>]*/?>', '', cleaned, flags=re.IGNORECASE)
    return cleaned


def _extract_latest_date(html: str) -> datetime | None:
    """Parse HTML text for date patterns and return the most recent one.

    Strips invisible elements (head, script, style, meta) first so that only
    dates from the rendered page content are considered.

    Args:
        html: Raw HTML content to scan for dates.

    Returns:
        The most recent datetime found, or None if no dates detected.
    """
    # Only scan visible page content — strip head, scripts, styles, meta tags
    visible_html = _strip_non_visible_html(html)

    dates: list[datetime] = []
    now = datetime.now(tz=timezone.utc)

    for pattern, fmt in _DATE_PATTERNS:
        matches = re.findall(pattern, visible_html)
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
def recency_check(url: str, rss_url: str = "") -> dict[str, Any]:
    """Check how recently a blog or forum was updated by scanning for dates in its HTML.

    Fetches the page and extracts date patterns to find the most recent post date.
    If an rss_url is provided, checks the RSS feed first for more accurate dates.

    Args:
        url: The full URL of the blog/forum to check.
        rss_url: Optional RSS feed URL to check first (more reliable than HTML scraping).

    Returns:
        A dict with keys:
          - url: the URL checked
          - last_post_date: ISO-8601 date string of the most recent post (or null)
          - days_since_last_post: int days since the last post (or null)
          - recency_status: "active" if within 365 days, "outdated" otherwise, "unknown" if no dates found
          - error: str or None
    """
    # Try RSS feed first — most reliable source of dates
    rss_date = _extract_date_from_rss(rss_url) if rss_url else None
    if rss_date:
        days_since = (datetime.now(tz=timezone.utc) - rss_date).days
        status = "active" if days_since <= 365 else "outdated"
        logger.info("[RECENCY_CHECK] url=%s rss_date=%s days=%d status=%s", url, rss_date.date(), days_since, status)
        return {
            "url": url,
            "last_post_date": rss_date.strftime("%Y-%m-%d"),
            "days_since_last_post": days_since,
            "recency_status": status,
            "error": None,
        }

    # Fall back to HTML scraping
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
        visible_html = _strip_non_visible_html(content)
        latest = _llm_extract_date(url, visible_html)

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
