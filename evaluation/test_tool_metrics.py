"""Minimal test: run the workflow agent for one team and inspect tool_metrics.

This bypasses the full evaluation runner to directly inspect what Strands
reports in result.metrics after an agent run.

FINDING: Models (Sonnet, Haiku, Nova, GLM) bypass gateway__WebSearch entirely.
Instead they pass Google search URLs to web_fetch. Only Opus actually calls
the MCP web search tool. The tool_metrics dict only contains tools that were
actually invoked — if gateway__WebSearch is missing from the keys, the model
never called it.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "blog_search"))

from dotenv import load_dotenv

load_dotenv(ROOT / "blog_search" / ".env")

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
os.environ["MODEL_ID"] = MODEL_ID

from blog_search_agent import (
    _create_workflow_agent,
    _build_non_rss_workflow_message,
)


class ToolCallTracker:
    """Callback handler that records every tool call with its inputs."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        if "current_tool_use" in kwargs and "tool_result" not in kwargs:
            tool = kwargs["current_tool_use"]
            self.calls.append({
                "name": tool.get("name", ""),
                "input": tool.get("input", {}),
            })


def run_test():
    """Run the workflow agent for Columbia Lions and dump metrics."""
    non_rss_blogs = [
        {"url": "https://www.columbiaspectator.com/sports/mens-basketball/"},
        {"url": "https://ivyleague.chat/"},
    ]
    team = "Columbia Lions"
    sport = "Basketball"
    events = ["transfer", "commitment", "decommitment", "coaching_change", "nil_deal"]
    datetime_start = "2026-07-29T00:00:00Z"
    datetime_end = "2026-08-05T00:00:00Z"

    # First, check if the MCP gateway is actually serving tools
    from blog_search_agent import _get_gateway_mcp_client
    print("\n--- MCP Gateway Health Check ---")
    gw = _get_gateway_mcp_client()
    gw.start()
    try:
        mcp_tools = gw.list_tools_sync()
        print(f"  MCP tools available: {len(mcp_tools)}")
        for t in mcp_tools:
            print(f"    - {t.name}")
        if not mcp_tools:
            print("  ⛔ GATEWAY RETURNED 0 TOOLS — gateway__WebSearch is NOT available!")
            print("  The model cannot call web search because the tool doesn't exist.")
            print("  Check: Is the AgentCore Gateway deployed? Is the token valid?")
    except Exception as e:
        print(f"  ⛔ MCP Gateway connection error: {e}")
    print()

    tracker = ToolCallTracker()
    agent = _create_workflow_agent(debug=False, callback_handler=tracker)

    # Check what tools the agent actually has available
    print("Agent tool registry:")
    for name in sorted(agent.tool_registry.registry.keys()):
        print(f"  - {name}")
    print()

    message = _build_non_rss_workflow_message(
        non_rss_blogs, team, sport, events, datetime_start, datetime_end,
    )

    print(f"Model: {MODEL_ID}")
    print("=" * 60)
    print("Running agent for Columbia Lions (2 non-RSS blogs)...")
    print("=" * 60)

    result = agent(message)

    # === Metrics from Strands ===
    print("\n" + "=" * 60)
    print("STRANDS METRICS (result.metrics.tool_metrics)")
    print("=" * 60)
    print(f"  Keys present: {list(result.metrics.tool_metrics.keys())}")
    for tool_name, metric in result.metrics.tool_metrics.items():
        print(f"  {tool_name}: call_count={metric.call_count}")

    search_metrics = result.metrics.tool_metrics.get("gateway__WebSearch")
    print(f"\n  gateway__WebSearch in tool_metrics: {search_metrics is not None}")
    if search_metrics:
        print(f"  gateway__WebSearch.call_count: {search_metrics.call_count}")
    else:
        print("  *** MODEL DID NOT CALL gateway__WebSearch ***")

    # === Tool calls from callback tracker ===
    print("\n" + "=" * 60)
    print(f"CALLBACK TRACKER: {len(tracker.calls)} total tool calls")
    print("=" * 60)

    web_search_calls = [c for c in tracker.calls if c["name"] == "gateway__WebSearch"]
    web_fetch_calls = [c for c in tracker.calls if c["name"] == "web_fetch"]
    other_calls = [c for c in tracker.calls if c["name"] not in ("gateway__WebSearch", "web_fetch")]

    print(f"  gateway__WebSearch calls: {len(web_search_calls)}")
    print(f"  web_fetch calls: {len(web_fetch_calls)}")
    if other_calls:
        print(f"  other calls: {[c['name'] for c in other_calls]}")

    # Check if web_fetch is being used as a search workaround
    search_url_fetches = []
    for c in web_fetch_calls:
        inp = c["input"]
        if isinstance(inp, dict):
            url = inp.get("url", "")
        elif isinstance(inp, str):
            url = inp
        else:
            url = str(inp)
        if any(s in url for s in ["google.com/search", "bing.com/search", "duckduckgo.com"]):
            search_url_fetches.append(url)

    if search_url_fetches:
        print(f"\n  ⚠️  MODEL IS BYPASSING gateway__WebSearch!")
        print(f"  It passed {len(search_url_fetches)} search engine URLs to web_fetch instead.")

    # Show sample web_fetch inputs
    print(f"\n  Sample web_fetch inputs (first 3):")
    for c in web_fetch_calls[:3]:
        inp = c["input"]
        if isinstance(inp, dict):
            print(f"    - url: {inp.get('url', '?')[:100]}")
        else:
            print(f"    - (raw): {str(inp)[:100]}")

    # === Token usage ===
    print("\n" + "=" * 60)
    print("TOKEN USAGE")
    print("=" * 60)
    usage = result.metrics.accumulated_usage
    print(f"  Input tokens:  {usage.get('inputTokens', 0):,}")
    print(f"  Output tokens: {usage.get('outputTokens', 0):,}")
    print(f"  Total tokens:  {usage.get('totalTokens', 0):,}")


if __name__ == "__main__":
    run_test()
