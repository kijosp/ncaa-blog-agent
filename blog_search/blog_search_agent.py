"""Blog Search Agent — extracts sports events from fan blog URLs.

Deployed as an AgentCore Runtime agent. Can also be invoked by a chat agent
for interactive follow-up questions.

## Use Cases:
1. Batch extraction: "Find all injury/schedule/venue events for team X from these URLs"
2. Chat follow-up: "Can you check this URL and see if there's injury news?"
3. Detail drill-down: "Show me the exact paragraph about Caleb Foster's injury"

## Payload Schema (input):

{
  "prompt": "Find latest events for Colgate Raiders",   # User query (required)
  "team": "Colgate Raiders",                            # optional if in prompt
  "sport": "NCAA Men's Basketball",                     # optional
  "events": ["injury", "schedule_update"],              # optional, defaults to all
  "urls": [                                             # optional — reads from registry if omitted
    {"url": "https://...", "rss_url": "https://..." or null}
  ]
}

## Output Schema:

{
  "results": [
    {
      "sport": "NCAA Men's Basketball",
      "team": "Colgate Raiders",
      "event_category": "injury",
      "player_name": "Blake Forrest",
      "excerpt": "Without sophomore guard Blake Forrest...",
      "summary": "Sophomore guard missed most of 2025-26 season",
      "source_url": "https://...",
      "blog_post_date": "2026-04-03",
      "record_timestamp": "2026-07-10T00:00:00Z",
      "retrieval_method": "rss + web_fetch"
    }
  ],
  "web_search_raw_results": [...],
  "retrieval_diagnostics": {...}
}
"""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests as http_requests
from dotenv import load_dotenv
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.handlers.callback_handler import null_callback_handler
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from tools.rss_fetch import rss_fetch
from tools.web_fetch import web_fetch

logger = logging.getLogger(__name__)

# Load .env from the blog_search directory (falls back to parent)
_this_dir = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_this_dir, ".env")
if not os.path.exists(_env_path):
    _env_path = os.path.join(_this_dir, "..", "discovery", ".env")
load_dotenv(_env_path)


# ---------------------------------------------------------------------------
# Supported event categories
# ---------------------------------------------------------------------------

SUPPORTED_EVENTS = [
    "injury",           # Player injuries, health updates, return-to-play
    "schedule_update",  # Game time changes, postponements, cancellations
    "venue_update",     # Venue changes, location updates
    "roster_move",      # Transfers, commitments, decommitments, suspensions
    "coaching_change",  # Coaching hires, firings, resignations
]


# ---------------------------------------------------------------------------
# Auth & MCP Client
# ---------------------------------------------------------------------------

def _get_m2m_token() -> str:
    """Fetch an M2M access token from Cognito using client_credentials grant."""
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


def _create_gateway_mcp_client() -> MCPClient:
    """Create an MCP client for the AgentCore Gateway (provides WebSearch tool)."""
    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        raise ValueError("GATEWAY_URL environment variable is required")

    gateway_token = os.environ.get("GATEWAY_TOKEN") or _get_m2m_token()

    return MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            headers={"Authorization": f"Bearer {gateway_token}"},
        ),
        prefix="gateway",
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a sports event extraction agent. You search fan blogs and news sources for
a specific college team and extract structured event data.

You can handle two types of requests:
1. **Batch extraction**: Given a team, URLs, and event categories — find all matching events.
2. **Follow-up queries**: Given a specific URL or previous result — provide more detail,
   exact paragraphs, or answer questions about the content.

## Event Categories

- **injury**: Player injuries, health updates, return-to-play timelines, surgery
- **schedule_update**: Game time changes, postponements, cancellations, added games
- **venue_update**: Venue changes, location updates, arena closures
- **roster_move**: Transfers, portal entries, commitments, decommitments, suspensions
- **coaching_change**: Coaching hires, firings, resignations, interim appointments

## Retrieval Strategy (Cost-Optimized)

When performing batch extraction, follow this strategy IN ORDER:

### Step 1: RSS Feeds (FREE — do this first)
For every URL that has an RSS feed, call rss_fetch on it.
- Read ALL entries (title, description, link, date).
- Identify entries that relate to ANY of the requested event categories.
- For promising entries, note their link URL for follow-up with web_fetch.

### Step 2: Web Search for non-RSS URLs only (PAID — minimize)
For URLs without RSS, use web search: "site:{domain} {team} {keywords}"
- One search per non-RSS domain.

### Step 3: One general catch-all search (PAID — one per team)
Run ONE search: "{team} {sport} {keywords} 2025 2026"
- Catches news from sources outside our URL list.

### Step 4: Web Fetch for promising links (FREE)
Call web_fetch on specific article URLs found via RSS or web search.
- Do NOT fetch landing pages — only specific articles/posts.

For **follow-up queries** (user asking for more detail on a specific URL or result):
- Just call web_fetch on the URL and provide the requested information.

## Output Format

ALWAYS return a JSON object with this structure:
```json
{
  "results": [
    {
      "sport": "<sport name>",
      "team": "<team name>",
      "event_category": "<injury | schedule_update | venue_update | roster_move | coaching_change>",
      "player_name": "<player/coach name if applicable, else null>",
      "excerpt": "<exact quote from the source, 1-3 sentences>",
      "summary": "<your concise 1-sentence summary>",
      "source_url": "<the URL where this was found>",
      "blog_post_date": "<YYYY-MM-DD if determinable, else null>",
      "record_timestamp": "<current ISO timestamp>",
      "retrieval_method": "<rss | web_search | web_search + web_fetch | rss + web_fetch | web_fetch>"
    }
  ],
  "web_search_raw_results": [
    {
      "query": "<exact search query>",
      "results": [
        {"title": "<>", "url": "<>", "snippet": "<>", "from_target_domain": true/false}
      ]
    }
  ],
  "retrieval_diagnostics": {
    "urls_total": <number>,
    "urls_with_rss": <number>,
    "urls_without_rss": <number>,
    "web_searches_performed": <number>,
    "web_search_queries": ["<queries>"],
    "web_search_hit_target_domain": true/false,
    "rss_feeds_fetched": <number>,
    "web_fetches_performed": <number>,
    "events_found_via_rss": true/false,
    "events_found_via_web_search": true/false
  }
}
```

## Rules
- Search for ALL requested event categories, not just injuries.
- Each result must have sport, team, and event_category populated.
- ALWAYS do RSS first for URLs with feeds.
- Only use web search for non-RSS URLs + one catch-all.
- Use web_fetch on specific article URLs only.
- Deduplicate: same event from multiple methods → one result, note all methods.
- If no events found, return empty results with filled diagnostics.
- For follow-up queries, web_search_raw_results and retrieval_diagnostics can be minimal.
"""


# ---------------------------------------------------------------------------
# Payload → user message builder
# ---------------------------------------------------------------------------

def build_user_message(payload: dict) -> str:
    """Convert a structured payload into the user message sent to the LLM.

    Keeps the message focused on DATA only. The system prompt handles strategy.

    Args:
        payload: Structured input dict.

    Returns:
        A clean user message string for the agent.
    """
    # If there's a raw prompt (chat mode), use it directly with context
    prompt = payload.get("prompt", "")
    team = payload.get("team", "")
    sport = payload.get("sport", "NCAA Men's Basketball")
    events = payload.get("events", SUPPORTED_EVENTS)
    urls = payload.get("urls", [])

    lines = []

    # If user provided a free-form prompt, include it
    if prompt:
        lines.append(prompt)
        lines.append("")

    # Add structured context
    if team:
        lines.append(f"Team: {team}")
    if sport:
        lines.append(f"Sport: {sport}")
    lines.append(f"Event categories to detect: {', '.join(events)}")
    lines.append("")

    if urls:
        # Separate by RSS availability
        rss_urls = [u for u in urls if u.get("rss_url")]
        non_rss_urls = [u for u in urls if not u.get("rss_url")]

        if rss_urls:
            lines.append(f"URLs with RSS feeds ({len(rss_urls)}):")
            for u in rss_urls:
                lines.append(f"  - {u['url']}")
                lines.append(f"    RSS: {u['rss_url']}")
            lines.append("")

        if non_rss_urls:
            lines.append(f"URLs without RSS ({len(non_rss_urls)}):")
            for u in non_rss_urls:
                domain = urlparse(u["url"]).netloc
                lines.append(f"  - {u['url']} (domain: {domain})")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Debug callback handler
# ---------------------------------------------------------------------------

class DebugCallbackHandler:
    """Prints agent reasoning, tool calls, and results to stdout."""

    def __init__(self):
        self._current_role = None

    def __call__(self, **kwargs):
        if "data" in kwargs:
            # Streaming text from the model
            print(kwargs["data"], end="", flush=True)
        elif "current_tool_use" in kwargs and "tool_result" not in kwargs:
            # Tool call starting
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
            # Tool result
            result = kwargs.get("tool_result", {})
            status = result.get("status", "")
            content = result.get("content", [])
            if status == "error":
                print(f"  ❌ TOOL ERROR: {content}")
            else:
                # Print a brief summary of the result
                for item in content[:1]:
                    if isinstance(item, dict) and "text" in item:
                        text = item["text"][:300]
                        print(f"  ✓ TOOL RESULT: {text}...")
            print()


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_blog_search_agent(debug: bool = False) -> Agent:
    """Create the blog search agent.

    Args:
        debug: If True, prints tool calls and reasoning to stdout.

    Returns:
        A Strands Agent configured for event extraction.
    """
    gateway_client = _create_gateway_mcp_client()
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    model = BedrockModel(model_id=model_id, temperature=0.1)

    if debug:
        handler = DebugCallbackHandler()
    else:
        handler = null_callback_handler

    agent = Agent(
        name="blog_search_agent",
        model=model,
        tools=[gateway_client, rss_fetch, web_fetch],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=handler,
    )

    # In debug mode, print the system prompt and user message
    if debug:
        print(f"\n{'─'*60}")
        print(f"  SYSTEM PROMPT (first 500 chars):")
        print(f"{'─'*60}")
        print(SYSTEM_PROMPT[:500])
        print(f"  ... ({len(SYSTEM_PROMPT)} chars total)")
        print(f"{'─'*60}\n")

    return agent


# ---------------------------------------------------------------------------
# AgentCore Runtime entrypoint
# ---------------------------------------------------------------------------

try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext

    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invocations(payload: dict, context: RequestContext):
        """Main entrypoint — called by AgentCore Runtime on each request.

        Expected payload:
        {
            "prompt": "Find latest injury news for Colgate Raiders",
            "team": "Colgate Raiders",          # optional if in prompt
            "sport": "NCAA Men's Basketball",   # optional
            "events": ["injury"],               # optional, defaults to all
            "urls": [...]                       # optional, reads from registry if omitted
        }
        """
        prompt = payload.get("prompt")
        if not prompt and not payload.get("team"):
            yield {
                "status": "error",
                "error": "Provide either 'prompt' or 'team' in the payload.",
            }
            return

        try:
            agent = create_blog_search_agent(debug=False)
            user_message = build_user_message(payload)

            async for event in agent.stream_async(user_message):
                yield json.loads(json.dumps(dict(event), default=str))

        except Exception as e:
            logger.exception("Blog search agent failed")
            yield {"status": "error", "error": str(e)}

    if __name__ == "__main__":
        app.run()

except ImportError:
    logger.debug("bedrock_agentcore.runtime not available — running in local mode")
