"""Strands tool: blended web search via the Tavily API.

The Tavily API key is stored in AWS Secrets Manager (created by the CDK backend
stack) and read at runtime using the secret name from the
`TAVILY_API_KEY_SECRET_NAME` environment variable. The AgentCore Runtime role is
granted `secretsmanager:GetSecretValue` on that secret.

Blended-search behavior: Tavily's standard search `include_domains` is a strict
"only these domains" filter with no soft-preference option. To give preferred fan
sites a *nudge* without excluding the rest of the web, this tool (when preferred
domains are supplied) runs two searches — one focused on the preferred domains and
one wide open — then merges and de-duplicates the results, flagging which ones came
from the preferred sites.

Typical agent flow:
  1. Call `lookup_school_blogs("Duke")` to get verified fan-blog URLs.
  2. Pass those URLs (or their domains) to `tavily_search(..., prefer_domains=[...])`.
"""

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import boto3
import requests
from strands import tool

logger = logging.getLogger(__name__)

# Tavily REST search endpoint. Auth is sent as a Bearer token in the header.
_TAVILY_SEARCH_URL: str = "https://api.tavily.com/search"
# Network timeout (seconds) for each Tavily HTTP call. Fail loudly on a hang rather
# than blocking the agent turn indefinitely.
_REQUEST_TIMEOUT_SECONDS: int = 30
# Max results requested per individual Tavily search (focused and wide each).
_MAX_RESULTS_PER_SEARCH: int = 5
# Allowed values for the tavily_search `scope` argument.
_VALID_SCOPES: frozenset[str] = frozenset({"focused", "wide", "both"})


def _get_tavily_api_key() -> str:
    """Retrieve the Tavily API key from AWS Secrets Manager.

    The secret name comes from the TAVILY_API_KEY_SECRET_NAME environment variable
    (set on the AgentCore Runtime by the CDK backend stack). The value is cached on
    the function object after the first successful lookup to avoid a Secrets Manager
    call on every search.

    Returns:
        The Tavily API key string.

    Raises:
        ValueError: If the TAVILY_API_KEY_SECRET_NAME environment variable is unset.
            We fail loudly instead of defaulting, so a misconfiguration is obvious.
    """
    cached: str | None = getattr(_get_tavily_api_key, "_cache", None)
    if cached is not None:
        return cached

    secret_name: str | None = os.environ.get("TAVILY_API_KEY_SECRET_NAME")
    if not secret_name:
        raise ValueError(
            "TAVILY_API_KEY_SECRET_NAME environment variable is required to read "
            "the Tavily API key."
        )

    region: str = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client(service_name="secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    api_key: str = response["SecretString"].strip()

    _get_tavily_api_key._cache = api_key  # type: ignore[attr-defined]
    return api_key


def _normalize_domain(value: str) -> str:
    """Reduce a domain or full URL to a bare lowercase hostname.

    Accepts either a hostname ("forums.dukebasketballreport.com") or a full URL
    ("https://forums.dukebasketballreport.com/board") and returns just the host.
    The leading "www." is stripped so matching is consistent.

    Args:
        value: A domain or URL string.

    Returns:
        The normalized hostname (empty string if none could be parsed).
    """
    text: str = value.strip()
    # urlparse only populates netloc when a scheme (//) is present.
    host: str = urlparse(text).netloc if "//" in text else text.split("/")[0]
    return host.lower().removeprefix("www.")


def _tavily_request(
    *, api_key: str, query: str, include_domains: list[str] | None
) -> dict[str, Any]:
    """Make a single Tavily search request and return the parsed JSON response.

    Args:
        api_key: The Tavily API key.
        query: The natural-language search query.
        include_domains: Optional hard domain filter. When provided, Tavily returns
            results ONLY from these domains. None/empty means a full web search.

    Returns:
        The parsed Tavily response as a dict (keys include "answer" and "results").

    Raises:
        requests.HTTPError: If Tavily returns a non-2xx status (e.g. 401 bad key,
            429 rate limited). Raised so failures are visible to the agent.
    """
    payload: dict[str, Any] = {
        "query": query,
        "search_depth": "basic",
        "max_results": _MAX_RESULTS_PER_SEARCH,
        "include_answer": True,
    }
    if include_domains:
        payload["include_domains"] = include_domains

    response = requests.post(
        url=_TAVILY_SEARCH_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _merge_results(
    *,
    focused: dict[str, Any],
    wide: dict[str, Any],
    preferred_domains: list[str],
) -> list[dict[str, Any]]:
    """Merge focused and wide Tavily result sets, de-duplicating by URL.

    Results from the focused (preferred-domain) search are listed first and flagged
    with from_preferred_domain=True. Wide-search results are appended unless their
    URL was already seen; each is flagged based on whether its host matches one of
    the preferred domains. Within the combined list, preferred results sort ahead of
    the rest, then by descending relevance score.

    Args:
        focused: Tavily response from the preferred-domain search.
        wide: Tavily response from the full web search.
        preferred_domains: Normalized preferred hostnames (for flagging wide hits).

    Returns:
        A merged, de-duplicated list of result dicts. Each has title, url, content,
        score, and from_preferred_domain.
    """
    merged: dict[str, dict[str, Any]] = {}

    def _add(raw: dict[str, Any], *, is_preferred: bool) -> None:
        """Insert one raw Tavily result into the merge map keyed by URL."""
        url: str = raw.get("url") or ""
        if not url or url in merged:
            return
        host: str = _normalize_domain(url)
        # A wide-search hit may still belong to a preferred domain; flag it if so.
        # Host-based match includes subdomains (e.g. "x.com" matches "forum.x.com").
        on_preferred: bool = is_preferred or any(
            host == d or host.endswith("." + d) for d in preferred_domains
        )
        merged[url] = {
            "title": raw.get("title"),
            "url": url,
            "content": raw.get("content"),
            "score": raw.get("score"),
            "from_preferred_domain": on_preferred,
        }

    for raw in focused.get("results", []):
        _add(raw, is_preferred=True)
    for raw in wide.get("results", []):
        _add(raw, is_preferred=False)

    # Preferred results first, then highest relevance score first.
    return sorted(
        merged.values(),
        key=lambda r: (not r["from_preferred_domain"], -(r["score"] or 0.0)),
    )


@tool
def tavily_search(
    query: str,
    prefer_domains: list[str] | None = None,
    scope: str = "both",
) -> str:
    """Search the web with Tavily, with selectable scope over preferred fan sites.

    Use this to find current information (news, blog posts, forum discussions). For
    NCAA school research, first call `lookup_school_blogs` and pass that school's
    verified URLs (or domains) as `prefer_domains`. The `scope` argument controls how
    those preferred sites are used — ask the user which they want:
      - "focused": search ONLY the preferred domains (requires prefer_domains).
      - "wide": ignore preferred domains and search the whole web.
      - "both" (default): run a focused search AND a wide search, then merge the
        results so fan communities are prioritized without excluding the broader web.

    Args:
        query: The natural-language search query. Keep it concise (under ~400 chars).
        prefer_domains: Optional list of domains or full URLs to prefer
            (e.g. ["https://forums.dukebasketballreport.com/"]). Required for
            "focused" and "both" scopes; ignored for "wide".
        scope: One of "focused", "wide", or "both" (default). Controls how
            prefer_domains is applied (see above).

    Returns:
        A JSON string containing:
          - "scope": the scope that was used
          - "preferred_domains": the normalized domains that were prioritized
          - "fan_site_answer": Tavily's synthesized answer from the focused search
            (null for "wide" scope)
          - "web_answer": Tavily's synthesized answer from the wide search
            (null for "focused" scope)
          - "results": merged, de-duplicated results, each with title, url, content,
            score, and from_preferred_domain (preferred sites listed first)

    Raises:
        ValueError: If scope is not one of the allowed values, or if a
            domain-dependent scope ("focused"/"both") is requested without any
            usable prefer_domains. We fail loudly rather than silently degrading.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"Invalid scope {scope!r}; expected one of {sorted(_VALID_SCOPES)}."
        )

    api_key: str = _get_tavily_api_key()

    # Normalize and de-duplicate the preferred domains, dropping anything unparseable.
    normalized_domains: list[str] = []
    for value in prefer_domains or []:
        host: str = _normalize_domain(value)
        if host and host not in normalized_domains:
            normalized_domains.append(host)

    if scope in ("focused", "both") and not normalized_domains:
        raise ValueError(
            f"scope={scope!r} requires at least one usable domain in prefer_domains."
        )

    # Wide-only: a single full web search.
    if scope == "wide":
        logger.info("[TAVILY] wide search query=%r", query)
        wide = _tavily_request(api_key=api_key, query=query, include_domains=None)
        return json.dumps(
            {
                "scope": scope,
                "preferred_domains": [],
                "fan_site_answer": None,
                "web_answer": wide.get("answer"),
                "results": _merge_results(
                    focused={"results": []}, wide=wide, preferred_domains=[]
                ),
            }
        )

    # Focused-only: a single search restricted to the preferred domains.
    if scope == "focused":
        logger.info(
            "[TAVILY] focused search query=%r domains=%s", query, normalized_domains
        )
        focused = _tavily_request(
            api_key=api_key, query=query, include_domains=normalized_domains
        )
        return json.dumps(
            {
                "scope": scope,
                "preferred_domains": normalized_domains,
                "fan_site_answer": focused.get("answer"),
                "web_answer": None,
                "results": _merge_results(
                    focused=focused,
                    wide={"results": []},
                    preferred_domains=normalized_domains,
                ),
            }
        )

    # Both (blended): focused (preferred domains only) + wide (whole web), merged.
    logger.info(
        "[TAVILY] blended search query=%r prefer_domains=%s", query, normalized_domains
    )
    focused = _tavily_request(
        api_key=api_key, query=query, include_domains=normalized_domains
    )
    wide = _tavily_request(api_key=api_key, query=query, include_domains=None)

    return json.dumps(
        {
            "scope": scope,
            "preferred_domains": normalized_domains,
            "fan_site_answer": focused.get("answer"),
            "web_answer": wide.get("answer"),
            "results": _merge_results(
                focused=focused, wide=wide, preferred_domains=normalized_domains
            ),
        }
    )
