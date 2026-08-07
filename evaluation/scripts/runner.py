"""Run the blog search agent workflow with configurable models and token tracking."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "blog_search"))

from .pricing import MODEL_CONFIGS, compute_run_cost
from .registry_sampler import load_registry

RESULTS_DIR = Path(__file__).parent.parent / "results"

_LOCAL_REGISTRY = None


def _get_local_registry() -> dict:
    """Load local registry JSON and index by team name."""
    global _LOCAL_REGISTRY
    if _LOCAL_REGISTRY is None:
        entries = load_registry()
        _LOCAL_REGISTRY = {entry["team"]: entry for entry in entries}
    return _LOCAL_REGISTRY


def _local_get_team_urls(team_name: str) -> dict:
    """Replacement for DynamoDB-based get_team_urls using local registry JSON."""
    registry = _get_local_registry()
    entry = registry.get(team_name)
    if not entry:
        return {
            "team": team_name,
            "blogs": [],
            "total_urls": 0,
            "searchable_urls": 0,
            "urls_with_rss": 0,
            "error": f"Team '{team_name}' not found in local registry",
        }
    blogs = entry.get("blogs", [])
    searchable = [
        b for b in blogs
        if b.get("accessible", False)
        and b.get("recency_status") != "outdated"
    ]
    with_rss = [b for b in searchable if b.get("rss_url")]
    return {
        "team": team_name,
        "blogs": searchable,
        "total_urls": len(blogs),
        "searchable_urls": len(searchable),
        "urls_with_rss": len(with_rss),
        "error": None,
    }



def _patch_model_ids(model_config: dict):
    """Override environment variables, module-level model IDs, and registry lookup."""
    os.environ["MODEL_ID"] = model_config["model_id"]
    os.environ["RSS_EXTRACTION_MODEL_ID"] = model_config["rss_model_id"]
    os.environ["WEB_EXTRACTION_MODEL_ID"] = model_config["web_model_id"]

    import blog_search_agent
    import tools.llm_extractor as extractor
    import tools.registry_reader as registry_reader

    extractor.RSS_EXTRACTION_MODEL_ID = model_config["rss_model_id"]
    extractor.WEB_EXTRACTION_MODEL_ID = model_config["web_model_id"]
    registry_reader.get_team_urls = _local_get_team_urls
    blog_search_agent.get_team_urls = _local_get_team_urls


async def run_single_team(
    team: str,
    model_config: dict,
    lookback_hours: float = 24,
    reference_date: str = "",
) -> dict:
    """Run the blog search workflow for a single team with token tracking."""
    from blog_search_agent import run_blog_search_workflow
    from tools.llm_extractor import get_accumulated_tokens, reset_token_accumulator

    _patch_model_ids(model_config)
    reset_token_accumulator()

    start_time = time.time()

    result = await run_blog_search_workflow(
        team=team,
        lookback_hours=lookback_hours,
        reference_date=reference_date,
        debug=False,
    )

    elapsed = time.time() - start_time
    extraction_tokens = get_accumulated_tokens()

    events = result.get("results", []) if result.get("status") == "success" else []
    diagnostics = result.get("retrieval_diagnostics", {})

    return {
        "team": team,
        "events": events,
        "events_count": len(events),
        "token_usage": {
            "agent_input_tokens": diagnostics.get("agent_input_tokens", 0),
            "agent_output_tokens": diagnostics.get("agent_output_tokens", 0),
            "rss_input_tokens": extraction_tokens["rss_input"],
            "rss_output_tokens": extraction_tokens["rss_output"],
            "web_input_tokens": extraction_tokens["web_input"],
            "web_output_tokens": extraction_tokens["web_output"],
        },
        "web_searches": diagnostics.get("web_searches_performed", 0),
        "web_fetches": diagnostics.get("web_fetches_performed", 0),
        "rss_feeds_fetched": diagnostics.get("rss_feeds_fetched", 0),
        "latency_seconds": round(elapsed, 2),
        "errors": [result.get("error")] if result.get("status") == "error" else [],
    }


async def run_evaluation(
    teams: list[str],
    model_config: dict,
    lookback_hours: float = 24,
    reference_date: str = "",
    save_path: Path | None = None,
) -> dict:
    """Run evaluation across all teams for a given model configuration.

    Args:
        teams: List of team names to evaluate.
        model_config: Dict with 'label', 'model_id', 'rss_model_id', 'web_model_id'.
        lookback_hours: How far back to search for events.
        reference_date: Optional ISO date string for reproducible time windows.
        save_path: Optional path to save the result JSON.

    Returns:
        Full evaluation result dict.
    """
    # Reset the MCP gateway client at the start of each experiment
    # to ensure a fresh connection and token.
    from blog_search_agent import _reset_gateway_client
    _reset_gateway_client()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    team_results = []

    for i, team in enumerate(teams):
        # Small delay between teams to avoid gateway connection/rate-limit issues
        if i > 0:
            await asyncio.sleep(5)
        print(f"  Running: {team} with {model_config['label']}...")
        result = await run_single_team(
            team, model_config, lookback_hours, reference_date
        )
        team_results.append(result)
        print(
            f"    -> {result['events_count']} events, "
            f"{result['latency_seconds']}s, "
            f"agent tokens: {result['token_usage']['agent_input_tokens']}in/"
            f"{result['token_usage']['agent_output_tokens']}out"
        )

    token_keys = ["agent_input_tokens", "agent_output_tokens",
                  "rss_input_tokens", "rss_output_tokens",
                  "web_input_tokens", "web_output_tokens"]
    totals = {k: 0 for k in token_keys}
    for r in team_results:
        for k in token_keys:
            totals[k] += r["token_usage"][k]

    total_input = totals["agent_input_tokens"] + totals["rss_input_tokens"] + totals["web_input_tokens"]
    total_output = totals["agent_output_tokens"] + totals["rss_output_tokens"] + totals["web_output_tokens"]
    total_web_searches = sum(r["web_searches"] for r in team_results)

    run_result = {
        "run_id": f"{model_config['label'].lower().replace(' ', '_')}_{timestamp}",
        "timestamp": timestamp,
        "model_config": model_config,
        "teams": teams,
        "lookback_hours": lookback_hours,
        "reference_date": reference_date,
        "team_results": team_results,
        "totals": {
            "events": sum(r["events_count"] for r in team_results),
            "web_searches": total_web_searches,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost_usd": round(
                compute_run_cost(model_config, totals, total_web_searches), 4
            ),
        },
    }

    if save_path is None:
        config_key = next(
            (k for k, v in MODEL_CONFIGS.items() if v["label"] == model_config["label"]),
            "custom",
        )
        save_path = RESULTS_DIR / f"{config_key}_{timestamp}.json"

    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(run_result, f, indent=2, default=str)
    print(f"  Saved: {save_path}")

    return run_result
