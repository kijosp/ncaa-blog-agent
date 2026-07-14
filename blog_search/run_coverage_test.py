"""Coverage Test: Measure how well the blog search agent extracts events.

Usage:
  # Chat mode (default teams):
  AWS_PROFILE=<profile> uv run python run_coverage_test.py

  # Workflow mode with lookback:
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --mode workflow --lookback-days 7

  # Specific teams and sport:
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --teams "Colgate Raiders" "Duke Blue Devils"

  # Debug mode (full trace: payload, prompts, tool calls, registry URLs):
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --debug

  # Limit URLs per team (chat mode only):
  AWS_PROFILE=<profile> uv run python run_coverage_test.py --max-urls 3
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blog_search_agent import (
    create_chat_agent,
    run_blog_search_workflow,
    extract_json_from_response,
    _debug_print_user_message,
)
from models import EVENT_TYPES

logger = logging.getLogger(__name__)

DEFAULT_TEAMS = ["Chicago State Cougars", "Colgate Raiders"]
DEFAULT_SPORT = "NCAA Men's Basketball"



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

        # Count by event type
        for r in result.get("results", []):
            cat = r.get("event_type", r.get("event_category", "unknown"))
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

    # Group by event_type
    by_category = {}
    for r in results_list:
        cat = r.get("event_type", r.get("event_category", "unknown"))
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
        "--teams", nargs="+", default=DEFAULT_TEAMS,
        help=f"Team names to test (default: {DEFAULT_TEAMS})",
    )
    parser.add_argument(
        "--sport", type=str, default=DEFAULT_SPORT,
        help=f"Sport name (default: {DEFAULT_SPORT})",
    )
    parser.add_argument(
        "--mode", choices=["chat", "workflow"], default="chat",
        help="Agent mode: 'chat' (default) or 'workflow' (deterministic pipeline)",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=7,
        help="Days to look back for events in workflow mode (default: 7)",
    )
    parser.add_argument(
        "--max-urls", type=int, default=0,
        help="Max URLs to test per team (0 = all accessible URLs, chat mode only)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print full debug trace (payload, prompts, tool calls, registry URLs)",
    )
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Suppress noisy third-party loggers
    for noisy_logger in [
        "botocore", "urllib3", "httpcore", "httpx", "asyncio",
        "mcp.client", "strands.tools.mcp", "strands.models",
        "strands.agent.agent_executor", "strands.agent.event_loop",
        "strands.agent.conversation_manager", "strands.telemetry",
    ]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Log to file
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = output_dir / f"coverage_test_{timestamp}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    sport = args.sport
    teams = args.teams

    print(f"\n{'#'*70}")
    print(f"  BLOG SEARCH COVERAGE TEST")
    print(f"  Mode:  {args.mode}")
    print(f"  Sport: {sport}")
    print(f"  Teams: {', '.join(teams)}")
    if args.mode == "workflow":
        print(f"  Lookback: {args.lookback_days} days")
    print(f"  Time:  {datetime.now(timezone.utc).isoformat()}")
    print(f"{'#'*70}\n")

    all_results = []

    if args.mode == "workflow":
        # Workflow mode: use the deterministic pipeline
        import asyncio

        for team in teams:
            print(f"\n--- Processing (workflow): {team} ---")
            print(f"  Running workflow pipeline (lookback_days={args.lookback_days})...")

            try:
                result = asyncio.run(run_blog_search_workflow(
                    team=team,
                    sport=sport,
                    lookback_days=args.lookback_days,
                    debug=args.debug,
                ))

                if result.get("status") == "error":
                    print(f"  ⚠️  Workflow error: {result.get('error')}")
                    all_results.append(result)
                else:
                    all_results.append(result)
                    _print_summary(team, result)

            except Exception as e:
                print(f"  ❌ Workflow error: {e}")
                logger.exception("Workflow failed for team: %s", team)
                all_results.append({
                    "team": team,
                    "sport": sport,
                    "results": [],
                    "retrieval_diagnostics": {"workflow_error": str(e)},
                })

    else:
        # Chat mode: fresh agent per team to avoid context bleed
        for team in teams:
            print(f"\n--- Processing (chat): {team} ---")

            message = f"Find all recent {sport} events for {team}. Look for injuries, roster changes, schedule changes, venue changes, and cancellations."

            if args.debug:
                _debug_print_user_message(message)

            print(f"\n  Running agent (this may take 30-60 seconds)...")

            try:
                agent = create_chat_agent(debug=args.debug)
                result = agent(message)
                response_text = str(result)

                # Try to parse structured output (agent may respond conversationally)
                parsed = extract_json_from_response(response_text)
                if parsed:
                    all_results.append(parsed)
                    _print_summary(team, parsed)
                else:
                    # Conversational response — store raw text
                    print(f"  Agent response (first 500 chars): {response_text[:500]}")
                    all_results.append({
                        "team": team,
                        "sport": sport,
                        "results": [],
                        "retrieval_diagnostics": {},
                        "raw_response": response_text[:2000],
                    })

            except Exception as e:
                print(f"  ❌ Agent error: {e}")
                logger.exception("Agent invocation failed for team: %s", team)
                all_results.append({
                    "team": team,
                    "sport": sport,
                    "results": [],
                    "retrieval_diagnostics": {"agent_error": str(e)},
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
            "mode": args.mode,
            "sport": sport,
            "teams_tested": teams,
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
