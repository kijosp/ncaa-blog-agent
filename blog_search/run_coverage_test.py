"""Coverage Test: Measure how well web search tools index our discovered blog URLs.

This script:
  1. Reads team blog URLs from the DynamoDB registry
  2. Invokes the blog search agent for each team
  3. Captures structured output including retrieval diagnostics
  4. Computes coverage metrics per team and overall
  5. Saves results to output/ as JSON

Usage:
  # Run for test teams defined in config:
  AWS_PROFILE=<profile> uv run python run_coverage_test.py

  # Run for specific teams:
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --teams "Colgate Raiders" "Chicago State Cougars"

  # Debug mode (streams agent reasoning):
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --debug

  # Limit URLs per team (faster test):
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --max-urls 3
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blog_search_agent import create_blog_search_agent, build_user_message, SUPPORTED_EVENTS
from tools.registry_reader import get_team_urls

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    """Load the blog_search config YAML."""
    config_path = Path(__file__).parent / "config" / "blog_search.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _build_payload(team: str, sport: str, blogs: list[dict], events: list[str] = None) -> dict:
    """Build the structured payload for the blog search agent.

    Args:
        team: Team name.
        sport: Sport name.
        blogs: List of blog dicts from registry (with url, rss_url fields).
        events: Event types to detect. Defaults to all supported events.

    Returns:
        Structured payload dict matching the agent's expected input.
    """
    if events is None:
        events = SUPPORTED_EVENTS

    urls = []
    for blog in blogs:
        urls.append({
            "url": blog.get("url", ""),
            "rss_url": blog.get("rss_url") or None,
        })

    return {
        "team": team,
        "sport": sport,
        "events": events,
        "urls": urls,
    }


def _extract_json_from_response(response_text: str) -> dict | None:
    """Extract JSON object from agent response text.

    The agent should return JSON in a code block, but may include
    surrounding text. This extracts the JSON object.
    """
    # Try to find JSON in code block first
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find a raw JSON object
    json_match = re.search(r"\{[\s\S]*\"retrieval_diagnostics\"[\s\S]*\}", response_text)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort: try the entire response
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return None


def _compute_metrics(results: list[dict]) -> dict:
    """Compute aggregate coverage metrics from test results.

    Args:
        results: List of parsed agent response dicts (one per team).

    Returns:
        Metrics dict with coverage rates and scores.
    """
    n_teams = len(results) or 1
    total_web_searches = 0
    rss_found_events = 0
    web_search_found_events = 0
    any_method_found_events = 0
    total_urls_with_rss = 0
    total_urls_without_rss = 0
    events_by_category = {}

    for result in results:
        diag = result.get("retrieval_diagnostics", {})
        total_web_searches += diag.get("web_searches_performed", 0)
        total_urls_with_rss += diag.get("urls_with_rss", 0)
        total_urls_without_rss += diag.get("urls_without_rss", 0)

        if diag.get("events_found_via_rss"):
            rss_found_events += 1
        if diag.get("events_found_via_web_search"):
            web_search_found_events += 1
        if result.get("results"):
            any_method_found_events += 1

        # Count by category
        for r in result.get("results", []):
            cat = r.get("event_category", "unknown")
            events_by_category[cat] = events_by_category.get(cat, 0) + 1

    return {
        "teams_tested": len(results),
        "total_web_searches": total_web_searches,
        "avg_web_searches_per_team": f"{total_web_searches / n_teams:.1f}",
        "total_urls_with_rss": total_urls_with_rss,
        "total_urls_without_rss": total_urls_without_rss,
        "events_found_via_rss": f"{rss_found_events}/{n_teams} teams ({rss_found_events/n_teams*100:.0f}%)",
        "events_found_via_web_search": f"{web_search_found_events}/{n_teams} teams ({web_search_found_events/n_teams*100:.0f}%)",
        "events_found_any_method": f"{any_method_found_events}/{n_teams} teams ({any_method_found_events/n_teams*100:.0f}%)",
        "events_by_category": events_by_category,
    }


def _print_summary(team: str, result: dict) -> None:
    """Print a human-readable summary of results for one team."""
    diag = result.get("retrieval_diagnostics", {})
    results_list = result.get("results", [])

    print(f"\n{'='*70}")
    print(f"  TEAM: {team}")
    print(f"{'='*70}")
    print(f"  URLs total: {diag.get('urls_total', '?')}")
    print(f"  URLs with RSS: {diag.get('urls_with_rss', '?')}")
    print(f"  URLs without RSS: {diag.get('urls_without_rss', '?')}")
    print(f"  Web searches performed: {diag.get('web_searches_performed', '?')}")
    print(f"  Web search queries: {diag.get('web_search_queries', [])}")
    print(f"  Web search hit target domain: {diag.get('web_search_hit_target_domain', '?')}")
    print(f"  RSS feeds fetched: {diag.get('rss_feeds_fetched', '?')}")
    print(f"  Web fetches performed: {diag.get('web_fetches_performed', '?')}")
    print(f"  ---")
    print(f"  Events found via RSS: {diag.get('events_found_via_rss', '?')}")
    print(f"  Events found via web search: {diag.get('events_found_via_web_search', '?')}")
    print(f"  ---")
    print(f"  Total events: {len(results_list)}")

    # Group by event_category
    by_category = {}
    for r in results_list:
        cat = r.get("event_category", "unknown")
        by_category.setdefault(cat, []).append(r)

    for cat, events in by_category.items():
        print(f"\n  [{cat}] ({len(events)} found):")
        for r in events[:3]:
            print(f"    • [{r.get('retrieval_method', '?')}] {r.get('summary', 'No summary')[:90]}")
            if r.get("blog_post_date"):
                print(f"      Date: {r['blog_post_date']} | Source: {r.get('source_url', '?')[:55]}")

    if not results_list:
        print("    (No events found from any method)")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Blog search indexing coverage test")
    parser.add_argument(
        "--teams", nargs="+",
        help="Team names to test (default: from config)",
    )
    parser.add_argument(
        "--max-urls", type=int, default=0,
        help="Max URLs to test per team (0 = all accessible URLs)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Stream agent reasoning to stdout",
    )
    parser.add_argument(
        "--config", type=str, default="config/blog_search.yaml",
        help="Path to config YAML",
    )
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Suppress noisy third-party loggers (raw HTTP streams, auth signatures, etc.)
    for noisy_logger in [
        "botocore", "urllib3", "httpcore", "httpx", "asyncio",
        "mcp.client", "strands.tools.mcp", "strands.models",
        "strands.agent.agent_executor", "strands.agent.event_loop",
        "strands.agent.conversation_manager", "strands.telemetry",
    ]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Also log to file
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = output_dir / f"coverage_test_{timestamp}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    # Load config
    config = _load_config()
    sport = config.get("sport", "men's basketball")
    teams = args.teams or config.get("test_teams", [])

    if not teams:
        print("ERROR: No teams specified. Use --teams or define test_teams in config.")
        sys.exit(1)

    print(f"\n{'#'*70}")
    print(f"  BLOG SEARCH INDEXING COVERAGE TEST")
    print(f"  Sport: {config.get('display_name', sport)}")
    print(f"  Teams: {', '.join(teams)}")
    print(f"  Time:  {datetime.now(timezone.utc).isoformat()}")
    print(f"{'#'*70}\n")

    # Create the agent once (reuse across teams)
    print("Initializing blog search agent...")
    agent = create_blog_search_agent(debug=args.debug)
    print("Agent ready.\n")

    all_results = []

    for team in teams:
        print(f"\n--- Processing: {team} ---")

        # Read URLs from registry
        registry_data = get_team_urls(team)
        if registry_data.get("error"):
            print(f"  ⚠️  Registry error: {registry_data['error']}")
            all_results.append({
                "team": team,
                "sport": sport,
                "results": [],
                "retrieval_diagnostics": {"error": registry_data["error"]},
                "registry_stats": registry_data,
            })
            continue

        blogs = registry_data.get("blogs", [])
        if not blogs:
            print(f"  ⚠️  No accessible URLs found in registry for {team}")
            all_results.append({
                "team": team,
                "sport": sport,
                "results": [],
                "retrieval_diagnostics": {"urls_searched": 0, "error": "No accessible URLs"},
                "registry_stats": registry_data,
            })
            continue

        # Optionally limit URLs
        if args.max_urls > 0:
            blogs = blogs[:args.max_urls]

        print(f"  Found {len(blogs)} searchable URLs ({registry_data['urls_with_rss']} with RSS)")
        for b in blogs:
            rss_note = " [RSS]" if b.get("rss_url") else ""
            print(f"    • {b['url'][:70]}{rss_note}")

        # Build payload and user message
        payload = _build_payload(team, sport, blogs)
        user_message = build_user_message(payload)

        if args.debug:
            print(f"\n  ┌─ USER MESSAGE TO AGENT ─────────────────────────")
            for line in user_message.strip().split("\n"):
                print(f"  │ {line}")
            print(f"  └─────────────────────────────────────────────────")

        print(f"\n  Running agent (this may take 30-60 seconds)...")

        try:
            result = agent(user_message)
            response_text = str(result)

            # Parse the structured output
            parsed = _extract_json_from_response(response_text)
            if parsed:
                parsed["registry_stats"] = {
                    "total_urls": registry_data["total_urls"],
                    "searchable_urls": registry_data["searchable_urls"],
                    "urls_with_rss": registry_data["urls_with_rss"],
                }
                all_results.append(parsed)
                _print_summary(team, parsed)
            else:
                print(f"  ⚠️  Could not parse structured JSON from agent response.")
                print(f"  Raw response (first 500 chars): {response_text[:500]}")
                all_results.append({
                    "team": team,
                    "sport": sport,
                    "results": [],
                    "retrieval_diagnostics": {"parse_error": True},
                    "raw_response": response_text[:2000],
                    "registry_stats": registry_data,
                })

        except Exception as e:
            print(f"  ❌ Agent error: {e}")
            logger.exception("Agent invocation failed for team: %s", team)
            all_results.append({
                "team": team,
                "sport": sport,
                "results": [],
                "retrieval_diagnostics": {"agent_error": str(e)},
                "registry_stats": registry_data,
            })

    # Compute and display aggregate metrics
    print(f"\n\n{'#'*70}")
    print(f"  AGGREGATE METRICS")
    print(f"{'#'*70}")
    metrics = _compute_metrics(all_results)
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    # Save results
    output_file = output_dir / f"coverage_test_{timestamp}.json"

    output_data = {
        "test_metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sport": config.get("display_name"),
            "teams_tested": teams,
            "search_tool": "agentcore_gateway_web_search",
        },
        "results": all_results,
        "metrics": metrics,
    }

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2, default=str)

    print(f"\n  Results saved to: {output_file}")
    print(f"  Log saved to:     {log_file}")
    print(f"{'#'*70}\n")


if __name__ == "__main__":
    main()
