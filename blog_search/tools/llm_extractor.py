"""Shared LLM event extraction utility.

Both rss_fetch and web_fetch call into this module to extract structured events
from raw content using a lightweight model (default: Haiku 4.5).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)

RSS_EXTRACTION_MODEL_ID = os.environ.get(
    "RSS_EXTRACTION_MODEL_ID", "us.amazon.nova-2-lite-v1:0"
)
WEB_EXTRACTION_MODEL_ID = os.environ.get(
    "WEB_EXTRACTION_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

_client = None


def _get_client():
    global _client
    if _client is None:
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        profile = os.environ.get("AWS_PROFILE")
        session = boto3.Session(profile_name=profile, region_name=region)
        _client = session.client("bedrock-runtime")
    return _client


_EXTRACTION_PROMPT = """\
Extract sports events from the following content.

Sport: {sport}
Team: {team}
Event types to detect: {event_types}
Date range: {date_start} to {date_end}
Source URL: {source_url}
Retrieval method: {retrieval_method}

Rules:
- Only extract events matching the specified event types.
- Only extract events for the specified team and sport.
- If blog_post_date is outside the date range, SKIP that event.
- If blog_post_date cannot be determined, set it to null.
- Return empty events array if no matching events found.
- excerpt must be an exact quote from the content (1-3 sentences).
- detected_at must be the current timestamp: {detected_at}

Return ONLY valid JSON (no markdown fencing, no surrounding text):
{{
  "events": [
    {{
      "sport": "{sport}",
      "team": "{team}",
      "event_type": "<one of: {event_types}>",
      "player_name": "<name or null>",
      "excerpt": "<exact quote, 1-3 sentences>",
      "summary": "<1-sentence summary>",
      "source_url": "{source_url}",
      "blog_post_date": "<YYYY-MM-DD or null>",
      "detected_at": "{detected_at}",
      "retrieval_method": "{retrieval_method}"
    }}
  ]
}}

Content:
---
{content}
---"""




def _parse_extraction_response(response_text: str) -> dict | None:
    """Parse JSON from Haiku's response, handling markdown fencing."""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
        return None


async def extract_events(
    content: str,
    source_url: str,
    retrieval_method: str,
    team: str,
    sport: str,
    event_types: list[str],
    date_start: str = "",
    date_end: str = "",
    model_id: str = "",
) -> dict:
    """Call the extraction model to extract events from content.

    Args:
        content: Raw text content (RSS entries or web page text).
        source_url: The URL the content came from.
        retrieval_method: How the content was retrieved (rss, web_fetch, etc.).
        team: Team name to filter for.
        sport: Sport to filter for.
        event_types: List of event types to detect.
        date_start: Start of date range (YYYY-MM-DD).
        date_end: End of date range (YYYY-MM-DD).
        model_id: Bedrock model ID to use. Defaults to WEB_EXTRACTION_MODEL_ID.

    Returns:
        {
            "events": [EventResult-compatible dicts],
            "extraction_model": str,
            "extraction_error": str | None,
        }
    """
    if not model_id:
        model_id = WEB_EXTRACTION_MODEL_ID
    detected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    prompt = _EXTRACTION_PROMPT.format(
        sport=sport,
        team=team,
        event_types=", ".join(event_types),
        date_start=date_start or "any",
        date_end=date_end or "any",
        source_url=source_url,
        retrieval_method=retrieval_method,
        detected_at=detected_at,
        content=content[:30000],  # cap content to avoid exceeding context
    )

    for attempt in range(2):
        try:
            response = await asyncio.to_thread(
                _get_client().converse,
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"temperature": 0.0, "maxTokens": 4096},
            )

            response_text = response["output"]["message"]["content"][0]["text"]
            parsed = _parse_extraction_response(response_text)

            if parsed and "events" in parsed:
                events = parsed["events"]
                logger.info(
                    "[LLM_EXTRACTOR] source=%s events=%d model=%s",
                    source_url[:60],
                    len(events),
                    model_id,
                )
                return {
                    "events": events,
                    "extraction_model": model_id,
                    "extraction_error": None,
                }

            if attempt == 0:
                prompt = (
                    "Your previous response was not valid JSON. "
                    "Return ONLY a valid JSON object with an 'events' array. "
                    "No markdown, no text before or after.\n\n" + prompt
                )
                continue

            logger.warning(
                "[LLM_EXTRACTOR] source=%s failed to parse after retry: %s",
                source_url[:60],
                response_text[:200],
            )
            return {
                "events": [],
                "extraction_model": model_id,
                "extraction_error": "Model returned invalid JSON after retry",
            }

        except Exception as e:
            logger.warning(
                "[LLM_EXTRACTOR] source=%s attempt=%d error=%s",
                source_url[:60],
                attempt + 1,
                str(e),
            )
            if attempt == 0:
                continue
            return {
                "events": [],
                "extraction_model": model_id,
                "extraction_error": str(e),
            }

    return {
        "events": [],
        "extraction_model": model_id,
        "extraction_error": "Unexpected extraction loop exit",
    }
