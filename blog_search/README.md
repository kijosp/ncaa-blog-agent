# Blog Search Agent

An AI-powered information extraction agent that finds injury news from team-specific fan blog URLs. The agent uses three retrieval strategies to maximize coverage, especially for niche blogs that may not be well-indexed by search engines.

## Purpose

This module serves two roles:

1. **Indexing Coverage Test** — Measures whether the AgentCore web search tool (and later Tavily/Google) actually indexes and returns results from our discovered fan blog URLs.
2. **Production Information Extraction** — The same agent powers the chat UI and batch event detection pipeline, extracting structured injury events from fan blogs.

## Architecture

```
blog_search/
├── blog_search_agent.py      # Core agent: Strands agent with 3 retrieval tools
├── run_coverage_test.py      # Test script: runs agent for test teams, measures coverage
├── tools/
│   ├── rss_fetch.py          # RSS/Atom feed parser with injury keyword filtering
│   ├── web_fetch.py          # Direct HTTP fetch with content extraction + injury detection
│   └── registry_reader.py    # Reads team URLs from DynamoDB discovery registry
├── config/
│   └── blog_search.yaml      # Agent config: prompts, test teams, metrics
├── output/                   # Test results (JSON, timestamped)
└── pyproject.toml            # Python dependencies
```

## How the Agent Works

For each team URL, the agent tries **all three retrieval strategies**:

| # | Method | What it does | When it works |
|---|--------|--------------|---------------|
| 1 | **Web Search** | `site:{domain} {team} injury` via AgentCore Gateway | URL is indexed by search engine |
| 2 | **RSS Fetch** | Parse the blog's RSS feed for injury entries | Blog has an RSS feed with recent posts |
| 3 | **Web Fetch** | Direct HTTP GET, extract text, scan for injury keywords | URL is accessible (not bot-blocked) |

The agent aggregates results from all methods and returns structured JSON:

```json
{
  "sport": "NCAA Men's Basketball",
  "team": "Colgate Raiders",
  "results": [
    {
      "source_url": "https://...",
      "event_type": "injury",
      "player_name": "Braeden Smith",
      "excerpt": "Smith will miss 2-3 weeks...",
      "summary": "Starting guard out with sprained ankle",
      "blog_post_date": "2026-06-28",
      "record_timestamp": "2026-07-02T13:00:00Z",
      "retrieval_method": "rss"
    }
  ],
  "retrieval_diagnostics": {
    "urls_searched": 4,
    "web_search_hit_target_domain": false,
    "rss_available": true,
    "rss_had_recent_entries": true,
    "web_fetch_successful": true,
    "web_fetch_had_injury_content": true
  }
}
```

The `retrieval_diagnostics` block is the key output for the coverage test — it tells you **which methods actually work** for each URL.

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Same AWS credentials and environment as the `discovery/` module

### Environment Variables

The blog_search module shares credentials with `discovery/`. It will look for `.env` in this directory first, then fall back to `discovery/.env`.

Required variables (same as discovery):
| Variable | Description |
|----------|-------------|
| `AWS_PROFILE` | AWS credentials profile |
| `AWS_DEFAULT_REGION` | Region (default: `us-east-1`) |
| `GATEWAY_URL` | AgentCore Gateway MCP endpoint |
| `COGNITO_DOMAIN` | Cognito domain for M2M auth |
| `COGNITO_CLIENT_ID` | Cognito machine client ID |
| `COGNITO_CLIENT_SECRET` | Cognito machine client secret |
| `REGISTRY_TABLE` | DynamoDB table (default: `blog-discovery-registry`) |

### Install

```bash
cd blog_search
uv sync
```

## Running the Coverage Test

### Default (test teams from config: Chicago State Cougars & Colgate Raiders)

```bash
AWS_PROFILE=<profile> uv run python run_coverage_test.py
```

### Specific teams

```bash
AWS_PROFILE=<profile> uv run python run_coverage_test.py \
  --teams "Colgate Raiders" "Chicago State Cougars" "Wofford Terriers"
```

### Debug mode (see agent reasoning)

```bash
AWS_PROFILE=<profile> uv run python run_coverage_test.py --debug
```

### Limit URLs per team (faster iteration)

```bash
AWS_PROFILE=<profile> uv run python run_coverage_test.py --max-urls 3
```

## Output

Results are saved to `output/coverage_test_<timestamp>.json` with:
- Per-team structured results with retrieval diagnostics
- Aggregate coverage metrics
- Registry stats (how many URLs discovered vs. accessible vs. have RSS)

### Key Metrics

| Metric | What it measures |
|--------|------------------|
| `web_search_hit_rate` | % of teams where web search found content from our target domains |
| `rss_coverage_rate` | % of teams with working RSS feeds |
| `web_fetch_success_rate` | % of teams where direct fetch worked |
| `injury_found_any_method` | % of teams where ANY method found injury content |

## Future: Chat UI Integration

The `blog_search_agent.py` module is designed to be imported by both:
- This test script (`run_coverage_test.py`)
- The production chat server (same agent, invoked per user query)
- A batch pipeline that runs periodically and saves events to a DynamoDB events table

The structured output schema is the same across all three use cases — the chat UI will format it for display, while the batch pipeline will persist it.

## Future: Tavily / Google Comparison

To add alternative search tools for A/B comparison:
1. Create `tools/tavily_search.py` wrapping the Tavily API
2. Create `tools/google_search.py` wrapping Google Custom Search API
3. Modify `blog_search_agent.py` to accept a `search_provider` parameter
4. Run `run_coverage_test.py` once per provider, compare the results
