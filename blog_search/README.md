# Blog Search Agent

Extracts sports events (injuries, roster moves, schedule changes, venue updates, cancellations) from college fan blogs and news sources for NCAA Men's Basketball (CBB) trading operations.

## What It Does

The agent searches team-specific fan blogs, RSS feeds, and the web to find actionable events.

## Two Modes

### Chat Mode (default)

Conversational agent for interactive use. Accepts natural language questions, searches for events, and responds with cited answers.

**Date range:** Controlled by `lookback_hours` (default: 24 hours). The agent searches for events from `now - lookback_hours` to `now` using ISO datetime strings. Configurable via `DEFAULT_LOOKBACK_HOURS` env var or the `lookback_hours` parameter when creating the agent. The agent can override this if the user specifies a different time range (e.g., "last week", "yesterday").

**Flow:**
1. User asks a question (e.g., "Any injury news for Duke Blue Devils?")
2. Agent calls `registry_lookup` to get the team's blog URLs (crawlable + non-outdated only)
3. For URLs with RSS: calls `rss_fetch` — Nova 2 Lite extracts events from feed entries
4. For URLs without RSS: calls `gateway__WebSearch` — reviews snippets — calls `web_fetch` on promising URLs — Haiku 4.5 extracts events from page content
5. Agent summarizes all events conversationally with source citations

**Features:**
- Multi-turn conversation (AgentCore memory for session persistence)
- Streaming output (status messages before each tool call to reduce perceived latency)
- Sport scope enforcement (only CBB)
- Event type scoping (user can request specific types; defaults to all)
- URL scoping (user can provide specific URLs instead of using registry)

### Workflow Mode

Hybrid deterministic + agent pipeline for scheduled/batch runs. Returns structured JSON.

**Date range:** Controlled by `lookback_hours` (default: 1.5 hours). Computes a datetime window from `reference_date - lookback_hours` to `reference_date` using ISO datetime strings. RSS entries are pre-filtered by timestamp before LLM extraction — only entries within the window are passed to the model. In production, `reference_date` is the current datetime (UTC). For testing, you can set it to a past date to validate event detection.

**Event types:** By default, the workflow searches for ALL event types defined in `EVENT_TYPES` (in `models.py`):
- `INJURY` — player injury reports (always highest priority)
- `CANCELLATION` — games called off
- `VENUE_CHANGE` — arena or location updates
- `SCHEDULE_CHANGE` — game date/time modifications
- `ROSTER` — transfers, commits, departures, suspensions

You can override this by passing a subset via the `events` parameter (e.g., `events=["INJURY", "ROSTER"]`). `INJURY` is always force-included even if not specified.

**Flow:**
1. **Registry lookup** (plain Python, no LLM) — fetches team's blog URLs from DynamoDB, filters to crawlable + non-outdated
2. **RSS feeds — parallel, no agent loop** — all RSS URLs called concurrently via `asyncio.gather`. Each `rss_fetch` fetches the feed, then Nova 2 Lite extracts events. Events validated against `EventResult` Pydantic schema; malformed ones dropped.
3. **Non-RSS URLs — agent loop** (sequential reasoning, parallel tool execution) — a Sonnet agent with `gateway__WebSearch` + `web_fetch` decides search queries and which URLs to fetch. When it requests multiple `web_fetch` calls in one turn, Strands executes them concurrently via `ConcurrentToolExecutor`. Each agent turn (think -> call tools -> think) is sequential.
4. **Aggregate** all events from steps 2 & 3 and return structured JSON.

**Output:** JSON with `results` (list of events) and `retrieval_diagnostics`.

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- AWS credentials with access to Bedrock (Sonnet, Haiku, Nova) and DynamoDB

### Install

```bash
cd blog_search
uv sync
```

### Environment Variables

Create a `.env` file in `blog_search/` with:

```env
AWS_PROFILE=your-profile
AWS_DEFAULT_REGION=us-east-1

# Gateway (AgentCore MCP)
GATEWAY_URL=https://your-gateway-url.amazonaws.com/mcp
COGNITO_DOMAIN=your-domain.auth.us-east-1.amazoncognito.com
COGNITO_CLIENT_ID=your-client-id
COGNITO_CLIENT_SECRET=your-client-secret

# Models (optional overrides)
# MODEL_ID=us.anthropic.claude-sonnet-4-6
RSS_EXTRACTION_MODEL_ID=us.amazon.nova-2-lite-v1:0
WEB_EXTRACTION_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0

# DynamoDB registry
REGISTRY_TABLE=blog-discovery-registry

# AgentCore memory (for multi-turn chat sessions)
MEMORY_ID=your-memory-id
```

### Create AgentCore Memory (one-time)

```python
from bedrock_agentcore.memory.controlplane import MemoryControlPlaneClient

client = MemoryControlPlaneClient(region_name="us-east-1")
memory = client.create_memory(
    name="blogSearchSessionMemory",
    description="Short-term session memory for blog search agent",
    event_expiry_days=30,
    wait_for_active=True,
)
print(f"MEMORY_ID={memory['id']}")
```

Add the printed ID to your `.env` as `MEMORY_ID=...`.

## Run

### Interactive Testing (Notebook)

```bash
cd blog_search
jupyter notebook test_agent_modes.ipynb
```

The notebook has configurable parameters at the top:

| Parameter | Mode | Default | Description |
|-----------|------|---------|-------------|
| `CHAT_LOOKBACK_DAYS` | Chat | 7 | How many days back to search |
| `WORKFLOW_LOOKBACK_HOURS` | Workflow | 1.5 | How many hours back from reference date |
| `WORKFLOW_REFERENCE_DATE` | Workflow | `""` (today) | Set to a past date (e.g. `"2026-07-15"`) for testing |
| `DEBUG` | Both | `False` | Enable verbose tool traces |

### Coverage Test (CLI)

```bash
cd blog_search
AWS_PROFILE=your-profile uv run python run_coverage_test.py --mode workflow --lookback-days 7
```

### AgentCore Runtime (Production)

```bash
cd blog_search
uv run python blog_search_agent.py
```

Starts the AgentCore Runtime server. Accepts payloads via the AgentCore invocation API.

## Project Structure

```
blog_search/
  blog_search_agent.py          # Main agent: chat + workflow modes, AgentCore entrypoint
  models.py                     # Pydantic schemas, EVENT_TYPES, SPORT_SCOPE constants
  prompts/
    chat_system_prompt.txt      # Chat mode system prompt
    workflow_system_prompt.txt  # Workflow agent prompt (non-RSS URLs only)
  tools/
    llm_extractor.py            # Shared LLM extraction (Nova for RSS, Haiku for web)
    rss_fetch.py                # Async RSS feed fetch + extraction
    web_fetch.py                # Async web page fetch + extraction
    registry_reader.py          # DynamoDB registry lookup
  test_agent_modes.ipynb        # Interactive test notebook
  run_coverage_test.py          # CLI coverage test harness
  .env                          # Environment config (not committed)
  pyproject.toml                # Python dependencies
```

## Event Types

Defined in `models.py` as `EVENT_TYPES`:

| Type | Description |
|------|-------------|
| `INJURY` | Player injury reports (highest priority — always included) |
| `ROSTER` | Transfers, commits, departures, suspensions |
| `SCHEDULE_CHANGE` | Game date/time modifications |
| `VENUE_CHANGE` | Arena or location updates |
| `CANCELLATION` | Games called off entirely |

To modify the event types the agent detects, edit the `EVENT_TYPES` list in `models.py`.

## Payload Schema

### Chat Mode

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `message` | Yes | — | Natural language query |
| `runtimeSessionId` | No | — | Session ID for multi-turn memory |

```json
{
  "message": "Has there been any injury news for Duke Blue Devils?",
  "runtimeSessionId": "session-abc-123"
}
```

### Workflow Mode

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `mode` | Yes | — | Must be `"workflow"` |
| `team` | Yes | — | Team name (e.g. "Colgate Raiders") |
| `sport` | No | `SPORT_SCOPE` (`"CBB"`) | Sport identifier (defined in `models.py`) |
| `events` | No | All `EVENT_TYPES` | Subset of event types to detect |
| `lookback_hours` | No | `1.5` | Hours back from current time to search |

```json
{
  "mode": "workflow",
  "team": "Colgate Raiders",
  "sport": "CBB",
  "events": ["INJURY", "ROSTER"],
  "lookback_hours": 24
}
```
