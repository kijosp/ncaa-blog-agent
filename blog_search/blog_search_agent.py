"""Blog Search Agent — extracts sports events from fan blog URLs.

Deployed as an AgentCore Runtime agent. Supports two modes:

1. **Chat mode** (default): Conversational agent invoked by users or by the
   enrichment agent. Accepts free-form natural language questions. Has access to
   registry_lookup, rss_fetch, web_fetch, and web search tools. Responds
   conversationally with cited sources.

2. **Workflow mode**: Hybrid deterministic + agent pipeline for scheduled/batch runs.

## Workflow Mode — Execution Flow:

  Step 1: Registry lookup (plain Python, no LLM)
    - Fetches team's blog URLs from DynamoDB
    - Only returns crawlable (accessible=True) + non-outdated URLs

  Step 2: RSS feeds — parallel, no agent loop
    - All RSS URLs are called concurrently via asyncio.gather
    - Each rss_fetch: fetches feed → Nova 2 Lite extracts events → returns structured events
    - Events are validated against EventResult Pydantic schema; malformed ones are dropped

  Step 3: Non-RSS URLs — agent loop (sequential reasoning, parallel tool execution)
    - A Sonnet agent is created with gateway__WebSearch + web_fetch tools
    - Agent decides search queries and which URLs to fetch (requires LLM reasoning)
    - When the agent requests multiple web_fetch calls in one turn, Strands executes
      them concurrently (ConcurrentToolExecutor)
    - Each agent "turn" (think → call tools → think) is sequential

  Step 4: Aggregate all events from Steps 2 & 3 and return

## Payload Schema (input):

### Chat mode (default):
{
  "message": "Has there been any injury news for Duke Blue Devils?",
  "runtimeSessionId": "session-abc-123"   # required for multi-turn memory
}

### Workflow mode:
{
  "mode": "workflow",
  "team": "Colgate Raiders",                            # REQUIRED
  "sport": "CBB",                                        # optional, defaults to SPORT["id"]
  "events": ["INJURY", "ROSTER"],                       # optional, defaults to all
  "lookback_hours": 24                                   # optional, default 24 (past day)
}
"""

import asyncio
import base64
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlparse

import requests as http_requests
from dotenv import load_dotenv
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.handlers.callback_handler import null_callback_handler
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from models import EVENT_TYPES, SPORT, BlogSearchResponse, EventResult, validate_response
from prompts import load_prompt
from tools.registry_reader import get_team_urls, registry_lookup
from tools.rss_fetch import rss_fetch
from tools.web_fetch import web_fetch

logger = logging.getLogger(__name__)

# Load .env from the blog_search directory (falls back to parent)
_this_dir = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_this_dir, ".env")
if not os.path.exists(_env_path):
    _env_path = os.path.join(_this_dir, "..", "discovery", ".env")
load_dotenv(_env_path)

MAX_VALIDATION_RETRIES = 2


# ---------------------------------------------------------------------------
# Auth & MCP Client
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_m2m_token() -> str:
    """Fetch an M2M access token from Cognito using client_credentials grant.

    Cached for the lifetime of the process (tokens typically valid ~1 hour,
    process restarts on deploy).
    """
    domain = os.environ.get("COGNITO_DOMAIN")
    client_id = os.environ.get("COGNITO_CLIENT_ID")
    client_secret = os.environ.get("COGNITO_CLIENT_SECRET")

    if not all([domain, client_id, client_secret]):
        raise ValueError(
            "COGNITO_DOMAIN, COGNITO_CLIENT_ID, and COGNITO_CLIENT_SECRET must be set"
        )

    resp = http_requests.post(
        f"https://{domain}/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in Cognito response: {resp.text}")
    return token


_gateway_client: MCPClient | None = None


def _get_gateway_mcp_client() -> MCPClient:
    """Get or create the cached MCP client for the AgentCore Gateway."""
    global _gateway_client
    if _gateway_client is not None:
        return _gateway_client

    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        raise ValueError("GATEWAY_URL environment variable is required")

    gateway_token = os.environ.get("GATEWAY_TOKEN") or _get_m2m_token()

    _gateway_client = MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            headers={"Authorization": f"Bearer {gateway_token}"},
        ),
        prefix="gateway",
    )
    return _gateway_client


# ---------------------------------------------------------------------------
# Payload → user message builders
# ---------------------------------------------------------------------------

def _format_url_section(blogs: list[dict]) -> list[str]:
    """Format blog URLs into message lines, split by RSS availability."""
    lines = []
    rss_blogs = [b for b in blogs if b.get("rss_url")]
    non_rss_blogs = [b for b in blogs if not b.get("rss_url")]

    if rss_blogs:
        lines.append(f"URLs with RSS feeds ({len(rss_blogs)}):")
        for b in rss_blogs:
            lines.append(f"  - {b['url']}")
            lines.append(f"    RSS: {b['rss_url']}")
        lines.append("")

    if non_rss_blogs:
        lines.append(f"URLs without RSS ({len(non_rss_blogs)}):")
        for b in non_rss_blogs:
            domain = urlparse(b["url"]).netloc
            lines.append(f"  - {b['url']} (domain: {domain})")
        lines.append("")

    return lines


def build_user_message(payload: dict) -> str:
    """Extract the user's natural language message from the payload.

    Chat mode accepts a free-form message — the agent handles interpretation,
    team extraction, and tool calls autonomously based on the system prompt.
    """
    return payload.get("message") or payload.get("prompt", "")


def _build_workflow_message(
    team: str,
    sport: str,
    events: list[str],
    blogs: list[dict],
    datetime_start: str,
    datetime_end: str,
) -> str:
    """Build the user message for workflow mode with pre-resolved URLs."""
    lines = [
        f"Team: {team}",
        f"Sport: {sport}",
        f"Event types to detect: {', '.join(events)}",
        f"Date range: {datetime_start} to {datetime_end}",
        "",
    ]
    lines.extend(_format_url_section(blogs))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Debug utilities
# ---------------------------------------------------------------------------

def _debug_print_section(title: str, content: str) -> None:
    """Print a labeled debug section."""
    print(f"\n{'━'*70}")
    print(f"  {title}")
    print(f"{'━'*70}")
    for line in content.rstrip().split("\n"):
        print(f"  │ {line}")
    print(f"{'━'*70}\n")


def _debug_print_payload(payload: dict) -> None:
    """Print the input payload as formatted JSON."""
    _debug_print_section("INPUT PAYLOAD", json.dumps(payload, indent=2, default=str))


def _debug_print_system_prompt(mode: str, prompt: str) -> None:
    """Print the full system prompt."""
    _debug_print_section(f"SYSTEM PROMPT [{mode.upper()} MODE]", prompt)


def _debug_print_user_message(message: str) -> None:
    """Print the user message sent to the agent."""
    _debug_print_section("USER MESSAGE → AGENT", message)


def _debug_print_registry_urls(team: str, blogs: list[dict]) -> None:
    """Print the blog URLs discovered from the registry for a team."""
    lines = [f"Team: {team}", f"Total searchable URLs: {len(blogs)}", ""]
    rss_blogs = [b for b in blogs if b.get("rss_url")]
    non_rss_blogs = [b for b in blogs if not b.get("rss_url")]
    if rss_blogs:
        lines.append(f"With RSS ({len(rss_blogs)}):")
        for b in rss_blogs:
            lines.append(f"  {b['url']}")
            lines.append(f"    ↳ RSS: {b['rss_url']}")
        lines.append("")
    if non_rss_blogs:
        lines.append(f"Without RSS ({len(non_rss_blogs)}):")
        for b in non_rss_blogs:
            lines.append(f"  {b['url']}")
        lines.append("")
    _debug_print_section("REGISTRY URLs", "\n".join(lines))


class DebugCallbackHandler:
    """Prints agent trace: reasoning, tool calls, and results to stdout."""

    def __call__(self, **kwargs):
        if "data" in kwargs:
            print(kwargs["data"], end="", flush=True)
        elif "current_tool_use" in kwargs and "tool_result" not in kwargs:
            tool = kwargs["current_tool_use"]
            name = tool.get("name", "")
            inp = tool.get("input", {})
            print(f"\n  🔧 TOOL CALL: {name}")
            if isinstance(inp, dict):
                for k, v in inp.items():
                    val_str = str(v)[:200]
                    print(f"       {k}: {val_str}")
            print()
        elif "tool_result" in kwargs:
            result = kwargs.get("tool_result", {})
            status = result.get("status", "")
            content = result.get("content", [])
            if status == "error":
                print(f"  ❌ TOOL ERROR: {content}")
            else:
                for item in content[:1]:
                    if isinstance(item, dict) and "text" in item:
                        text = item["text"][:300]
                        print(f"  ✓ TOOL RESULT: {text}...")
            print()


# ---------------------------------------------------------------------------
# Memory & Auth
# ---------------------------------------------------------------------------

def _extract_user_id(context) -> str:
    """Extract user ID from JWT token in request context."""
    headers = context.request_headers or {}
    auth_header = headers.get("Authorization", "") or headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Invalid or missing Authorization header")

    token = auth_header[7:]
    payload_segment = token.split(".")[1]
    payload_segment += "=" * (4 - len(payload_segment) % 4)
    payload = json.loads(base64.b64decode(payload_segment))

    user_id = payload.get("sub")
    if not user_id:
        raise ValueError("No 'sub' claim in JWT token")
    return user_id


def _create_session_manager(user_id: str, session_id: str):
    """Create AgentCore memory session manager for conversation history."""
    from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )

    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=user_id,
    )

    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=region,
    )


# ---------------------------------------------------------------------------
# Agent factories
# ---------------------------------------------------------------------------

def create_chat_agent(
    user_id: str | None = None,
    session_id: str | None = None,
    lookback_hours: float | None = None,
    debug: bool = False,
) -> Agent:
    """Create the blog search agent for chat mode.

    Has all tools: gateway (web search), rss_fetch, web_fetch, registry_lookup.
    When user_id and session_id are provided, AgentCore memory is attached for
    multi-turn conversation history.

    Args:
        lookback_hours: Number of hours to look back for events. Defaults to
            DEFAULT_LOOKBACK_HOURS env var (24 if not set). The agent can
            override this if the user specifies a different time range.
    """
    gateway_client = _get_gateway_mcp_client()
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    model = BedrockModel(model_id=model_id, temperature=0)

    now = datetime.now(timezone.utc)
    if lookback_hours is None:
        lookback_hours = float(os.environ.get("DEFAULT_LOOKBACK_HOURS", "24"))
    dt_start = now - timedelta(hours=lookback_hours)
    datetime_start_iso = dt_start.isoformat(timespec="seconds")
    datetime_end_iso = now.isoformat(timespec="seconds")

    system_prompt = load_prompt("chat_system_prompt").format(
        event_types=", ".join(EVENT_TYPES),
        sport_scope=SPORT["name"],
        date_range=f"{datetime_start_iso} to {datetime_end_iso}",
    )

    if debug:
        handler = DebugCallbackHandler()
        _debug_print_system_prompt("chat", system_prompt)
    else:
        handler = null_callback_handler

    session_manager = None
    if user_id and session_id:
        try:
            session_manager = _create_session_manager(user_id, session_id)
        except Exception as e:
            logger.warning("Failed to create session manager, continuing without memory: %s", e)

    return Agent(
        name="blog_search_chat",
        model=model,
        tools=[gateway_client, rss_fetch, web_fetch, registry_lookup],
        system_prompt=system_prompt,
        callback_handler=handler,
        **({"session_manager": session_manager} if session_manager else {}),
    )


def _create_workflow_agent(debug: bool = False) -> Agent:
    """Create the workflow agent for non-RSS URL processing.

    Only has gateway (web search) + web_fetch. RSS is handled programmatically
    via asyncio.gather before this agent is invoked.
    """
    gateway_client = _get_gateway_mcp_client()
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    model = BedrockModel(model_id=model_id, temperature=0.0)

    system_prompt = load_prompt("workflow_system_prompt").format(
        event_types=", ".join(EVENT_TYPES),
    )

    if debug:
        handler = DebugCallbackHandler()
        _debug_print_system_prompt("workflow", system_prompt)
    else:
        handler = null_callback_handler

    return Agent(
        name="blog_search_workflow",
        model=model,
        tools=[gateway_client, web_fetch],
        system_prompt=system_prompt,
        callback_handler=handler,
    )


# ---------------------------------------------------------------------------
# Workflow pipeline
# ---------------------------------------------------------------------------

def extract_json_from_response(response_text: str) -> dict | None:
    """Extract JSON object from agent response text.

    Uses balanced-brace counting to find the outermost JSON object that
    contains "retrieval_diagnostics", avoiding greedy regex issues.
    """
    # Try to find JSON in code block first (non-greedy, balanced)
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Find the first '{' and use balanced brace counting
    start = response_text.find("{")
    if start != -1:
        obj = _extract_balanced_json(response_text, start)
        if obj is not None:
            return obj

    # Last resort: try the entire response stripped
    try:
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        return None


def _extract_balanced_json(text: str, start: int) -> dict | None:
    """Extract a JSON object starting at `start` using balanced brace counting."""
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            if in_string:
                escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None




def _parse_and_validate_with_retry(
    agent: Agent,
    response_text: str,
    debug: bool = False,
) -> BlogSearchResponse | dict:
    """Parse and validate agent response. Re-prompt agent on validation errors.

    Returns a validated BlogSearchResponse on success, or an error dict on failure.
    The retry only re-prompts the agent for corrected JSON — it does NOT re-execute
    tool calls (RSS, web search, etc.) since those results are already in context.
    """
    for attempt in range(1 + MAX_VALIDATION_RETRIES):
        parsed = extract_json_from_response(response_text)
        if not parsed:
            if attempt < MAX_VALIDATION_RETRIES:
                logger.warning(
                    "[VALIDATION] Attempt %d: could not parse JSON, retrying",
                    attempt + 1,
                )
                correction_prompt = (
                    "Your previous response could not be parsed as valid JSON. "
                    "DO NOT call any tools. Just return ONLY the JSON object "
                    "with the exact schema: "
                    '{"results": [...], "retrieval_diagnostics": {...}}. '
                    "No markdown, no explanation — just the raw JSON."
                )
                result = agent(correction_prompt)
                response_text = str(result)
                continue
            return {
                "status": "error",
                "error": "Could not parse structured JSON from agent response",
                "raw_response": response_text[:2000],
            }

        validation_result = validate_response(parsed)
        if isinstance(validation_result, BlogSearchResponse):
            if attempt > 0:
                logger.info(
                    "[VALIDATION] Succeeded on retry attempt %d", attempt
                )
            return validation_result

        errors = validation_result
        if attempt < MAX_VALIDATION_RETRIES:
            logger.warning(
                "[VALIDATION] Attempt %d: schema errors: %s",
                attempt + 1,
                errors,
            )
            error_list = "\n".join(f"  - {e}" for e in errors)
            correction_prompt = (
                "Your JSON response has validation errors:\n"
                f"{error_list}\n\n"
                "DO NOT call any tools. Just fix these errors and return the "
                "corrected JSON object. Required fields per result: sport, team, "
                "event_type, excerpt, summary, source_url, detected_at, "
                "retrieval_method. "
                "Return ONLY the corrected JSON — no markdown, no explanation."
            )
            if debug:
                _debug_print_section("VALIDATION RETRY", correction_prompt)
            result = agent(correction_prompt)
            response_text = str(result)
        else:
            return {
                "status": "error",
                "error": f"Output validation failed after {MAX_VALIDATION_RETRIES} retries",
                "validation_errors": errors,
                "raw_response": response_text[:2000],
            }

    return {
        "status": "error",
        "error": "Unexpected validation loop exit",
    }


async def run_blog_search_workflow(
    team: str,
    sport: str = SPORT["id"],
    events: list[str] | None = None,
    lookback_hours: float = 1.5,
    debug: bool = False,
    reference_date: str = "",
) -> dict:
    """Execute the hybrid blog search workflow pipeline.

    Steps:
    1. Fetch team's blog URLs from DynamoDB registry (plain Python, no LLM).
    2. Direct parallel RSS calls for all RSS URLs (no agent loop needed).
    3. Agent loop for non-RSS URLs (needs search query reasoning).
    4. Return all events (date filtering is done by the RSS tool pre-LLM).

    Args:
        lookback_hours: How many hours back from reference_date to search.
            Default 1.5 (past 90 minutes). Use 0 for reference_date only.
        reference_date: Override today's date for testing (YYYY-MM-DD or ISO datetime).
            In production this is always empty (uses real datetime.now(UTC)).
    """
    if events is None:
        events = EVENT_TYPES
    elif "INJURY" not in events:
        events = ["INJURY"] + list(events)

    if debug:
        _debug_print_payload({
            "mode": "workflow",
            "team": team,
            "sport": sport,
            "events": events,
            "lookback_hours": lookback_hours,
            "reference_date": reference_date or "today",
        })

    # Step 1: Fetch URLs from registry (already filtered: accessible + not outdated)
    logger.info("[WORKFLOW] Fetching registry for team=%s", team)
    registry_data = get_team_urls(team)

    if registry_data.get("error"):
        return {
            "status": "error",
            "mode": "workflow",
            "team": team,
            "error": registry_data["error"],
        }

    blogs = registry_data.get("blogs", [])
    if not blogs:
        return {
            "status": "success",
            "mode": "workflow",
            "team": team,
            "results": [],
            "retrieval_diagnostics": {
                "urls_total": 0,
                "error": "No accessible URLs found in registry",
            },
        }

    if debug:
        _debug_print_registry_urls(team, blogs)

    # Compute datetime range (reference_date overrides now for testing)
    if reference_date:
        try:
            ref = datetime.fromisoformat(reference_date.replace("Z", "+00:00"))
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
        except ValueError:
            ref = datetime.fromisoformat(reference_date + "T23:59:59+00:00")
    else:
        ref = datetime.now(timezone.utc)
    datetime_end = ref.isoformat(timespec="seconds")
    if lookback_hours == 0:
        datetime_start = datetime_end
    else:
        start_dt = ref - timedelta(hours=lookback_hours)
        datetime_start = start_dt.isoformat(timespec="seconds")
    event_types_str = ",".join(events)

    # Split blogs by RSS availability
    rss_blogs = [b for b in blogs if b.get("rss_url")]
    non_rss_blogs = [b for b in blogs if not b.get("rss_url")]

    logger.info(
        "[WORKFLOW] Dispatching: %d RSS feeds (direct), %d non-RSS blogs (agent) "
        "date_range=%s to %s",
        len(rss_blogs), len(non_rss_blogs), datetime_start, datetime_end,
    )

    # Step 2: Direct parallel RSS calls (no agent loop)
    all_events = []
    rss_feeds_fetched = 0
    rss_errors = []

    if rss_blogs:
        rss_tasks = [
            rss_fetch(
                rss_url=b["rss_url"],
                team=team,
                datetime_start=datetime_start,
                datetime_end=datetime_end,
                event_types=event_types_str,
                sport=sport,
            )
            for b in rss_blogs
        ]
        rss_results = await asyncio.gather(*rss_tasks, return_exceptions=True)

        for result in rss_results:
            if isinstance(result, Exception):
                logger.warning("[WORKFLOW] RSS task failed: %s", result)
                rss_errors.append(str(result))
                continue
            rss_feeds_fetched += 1
            for event in result.get("events", []):
                try:
                    validated = EventResult.model_validate(event)
                    all_events.append(validated.model_dump())
                except Exception as e:
                    logger.warning("[WORKFLOW] Dropping malformed RSS event: %s", e)
            if result.get("error"):
                rss_errors.append(result["error"])

    events_from_rss = len(all_events)
    logger.info("[WORKFLOW] RSS phase complete: %d events from %d feeds", events_from_rss, rss_feeds_fetched)

    # Step 3: Agent loop for non-RSS blogs (needs search query reasoning)
    web_searches_performed = 0
    web_fetches_performed = 0

    if non_rss_blogs:
        try:
            agent = _create_workflow_agent(debug=debug)
            message = _build_non_rss_workflow_message(
                non_rss_blogs, team, sport, events, datetime_start, datetime_end,
            )
            if debug:
                _debug_print_user_message(message)

            result = agent(message)
            response_text = str(result)

            validated = _parse_and_validate_with_retry(agent, response_text, debug=debug)
            if isinstance(validated, BlogSearchResponse):
                web_events = [r.model_dump() for r in validated.results]
                all_events.extend(web_events)
                diag = validated.retrieval_diagnostics.model_dump()
                web_searches_performed = diag.get("web_searches_performed", 0)
                web_fetches_performed = diag.get("web_fetches_performed", 0)
            elif isinstance(validated, dict) and validated.get("status") == "error":
                logger.warning("[WORKFLOW] Non-RSS agent failed: %s", validated.get("error"))
        except Exception as e:
            logger.exception("[WORKFLOW] Non-RSS agent execution failed for team=%s", team)

    return {
        "status": "success",
        "mode": "workflow",
        "team": team,
        "sport": sport,
        "lookback_hours": lookback_hours,
        "date_range": {"start": datetime_start, "end": datetime_end},
        "results": all_events,
        "results_count": len(all_events),
        "retrieval_diagnostics": {
            "urls_total": len(blogs),
            "urls_with_rss": len(rss_blogs),
            "urls_without_rss": len(non_rss_blogs),
            "rss_feeds_fetched": rss_feeds_fetched,
            "web_searches_performed": web_searches_performed,
            "web_fetches_performed": web_fetches_performed,
            "events_found_via_rss": events_from_rss > 0,
            "events_found_via_web_search": len(all_events) > events_from_rss,
        },
        "registry_stats": {
            "total_urls": registry_data["total_urls"],
            "searchable_urls": registry_data["searchable_urls"],
            "urls_with_rss": registry_data["urls_with_rss"],
        },
    }


def _build_non_rss_workflow_message(
    non_rss_blogs: list[dict],
    team: str,
    sport: str,
    events: list[str],
    datetime_start: str,
    datetime_end: str,
) -> str:
    """Build user message for the agent handling only non-RSS URLs."""
    lines = [
        f"Team: {team}",
        f"Sport: {sport}",
        f"Event types to detect: {', '.join(events)}",
        f"Date range: {datetime_start} to {datetime_end}",
        f"Parameters to pass to web_fetch: team={team}, datetime_start={datetime_start}, "
        f"datetime_end={datetime_end}, event_types={','.join(events)}, sport={sport}",
        "",
        f"URLs without RSS ({len(non_rss_blogs)}):",
    ]
    for b in non_rss_blogs:
        domain = urlparse(b["url"]).netloc
        lines.append(f"  - {b['url']} (domain: {domain})")
    lines.append("")
    lines.append("NOTE: RSS feeds have already been processed separately. "
                 "Only handle these non-RSS URLs via web search + web_fetch.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AgentCore Runtime entrypoint
# ---------------------------------------------------------------------------

try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invocations(payload: dict, context: RequestContext):
        """Main entrypoint — routes to chat or workflow mode based on payload.

        mode="chat" (default): Conversational agent with all tools.
        mode="workflow": Deterministic pipeline for batch/scheduled runs.
        Pass "debug": true in payload to print full trace.
        """
        mode = payload.get("mode", "chat")
        debug = payload.get("debug", False)

        if mode == "workflow":
            team = payload.get("team")
            if not team:
                yield {"status": "error", "error": "Workflow mode requires 'team' field"}
                return

            result = await run_blog_search_workflow(
                team=team,
                sport=payload.get("sport", SPORT["id"]),
                events=payload.get("events"),
                lookback_hours=payload.get("lookback_hours", 1.5),
                debug=debug,
            )
            yield result

        else:
            # Chat mode (default) — accepts free-form natural language
            message = payload.get("message") or payload.get("prompt", "")
            if not message:
                yield {
                    "status": "error",
                    "error": "Chat mode requires a 'message' field.",
                }
                return

            session_id = payload.get("runtimeSessionId")
            try:
                user_id = _extract_user_id(context)
            except Exception:
                user_id = None

            try:
                if debug:
                    _debug_print_payload(payload)

                agent = create_chat_agent(
                    user_id=user_id,
                    session_id=session_id,
                    debug=debug,
                )

                if debug:
                    _debug_print_user_message(message)

                async for event in agent.stream_async(message):
                    yield json.loads(json.dumps(dict(event), default=str))

            except Exception as e:
                logger.exception("Blog search agent failed")
                yield {"status": "error", "error": str(e)}

    if __name__ == "__main__":
        app.run()

except ImportError:
    logger.debug("bedrock_agentcore.runtime not available — running in local mode")
