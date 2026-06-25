"""Tool: check if a URL is accessible via HTTP HEAD/GET.

Returns status code, response time, and whether the URL is reachable.
"""

import logging
import time
from typing import Any

import requests
from strands import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: int = 10

# Standard interpretation of HTTP status codes encountered during access checks.
# If a new code is encountered at runtime, it gets added with a generic label.
_STATUS_MEANINGS: dict[int, str] = {
    200: "ok",
    301: "redirect",
    302: "redirect",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    408: "timeout",
    409: "bot_blocked",
    410: "gone",
    429: "rate_limited",
    500: "server_error",
    502: "bad_gateway",
    503: "unavailable",
    504: "gateway_timeout",
    520: "cloudflare_error",
    521: "cloudflare_down",
    522: "cloudflare_timeout",
    530: "cloudflare_blocked",
}


def _get_status_label(code: int) -> str:
    """Get a human-readable status label for an HTTP status code.

    Args:
        code: HTTP status code.

    Returns:
        Label in format "CODE meaning" (e.g. "403 forbidden", "200 ok").
    """
    if code == 0:
        return "connection_error"
    if 200 <= code < 400:
        return "accessible"
    meaning = _STATUS_MEANINGS.get(code)
    if meaning is None:
        meaning = "unknown_error"
        _STATUS_MEANINGS[code] = meaning
        logger.info("[ACCESS_CHECK] New status code encountered: %d (added as '%s')", code, meaning)
    return f"{code} {meaning}"


@tool
def access_check(url: str) -> dict[str, Any]:
    """Check if a URL is accessible by making an HTTP request.

    Attempts HTTP HEAD first (lightweight). Falls back to GET if HEAD
    returns 405 Method Not Allowed. Reports status code and response time.

    Args:
        url: The full URL to check (e.g. "https://forums.example.com/").

    Returns:
        A dict with keys:
          - url: the URL checked
          - accessible: bool (True if status 200-399)
          - status_code: int HTTP status code (0 if connection failed)
          - response_time_ms: int milliseconds for the request
          - error: str or None (error message if connection failed)
    """
    start = time.time()
    try:
        method = "HEAD"
        resp = requests.head(url, timeout=_TIMEOUT_SECONDS, allow_redirects=True)
        if resp.status_code == 405:
            method = "GET"
            resp = requests.get(url, timeout=_TIMEOUT_SECONDS, allow_redirects=True, stream=True)
            resp.close()
        elapsed_ms = int((time.time() - start) * 1000)
        accessible = 200 <= resp.status_code < 400
        status_label = _get_status_label(resp.status_code)
        logger.info("[ACCESS_CHECK] url=%s status=%d label=%s", url, resp.status_code, status_label)
        logger.debug(
            "[ACCESS_CHECK] url=%s | method=%s | status_code=%d | label=%s | "
            "response_time=%dms | final_url=%s",
            url, method, resp.status_code, status_label, elapsed_ms, resp.url,
        )
        return {
            "url": url,
            "accessible": accessible,
            "status_code": resp.status_code,
            "status_label": status_label,
            "response_time_ms": elapsed_ms,
            "error": None,
        }
    except requests.RequestException as e:
        elapsed_ms = int((time.time() - start) * 1000)
        error_str = str(e)
        if "timed out" in error_str.lower():
            status_label = "0 timeout"
        else:
            status_label = "0 connection_error"
        logger.warning("[ACCESS_CHECK] url=%s error=%s label=%s", url, error_str, status_label)
        return {
            "url": url,
            "accessible": False,
            "status_code": 0,
            "status_label": status_label,
            "response_time_ms": elapsed_ms,
            "error": error_str,
        }
