#!/usr/bin/env python3
"""Orchestration script for the blog discovery pipeline.

Flow:
  1. Load domain config (YAML)
  2. Run team discovery agent → get all teams by conference
  3. Load existing registry (if any)
  4. Per team: run blog discovery agent → find/validate blogs
  5. Save updated registry as JSON

Usage:
    export AWS_PROFILE=fanduel
    export AWS_DEFAULT_REGION=us-east-1
    export GATEWAY_URL=<your-gateway-url>
    export GATEWAY_TOKEN=<your-m2m-token>
    python discovery/run_discovery.py --config discovery/config/ncaa_mbb.yaml
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# Add discovery directory to path for imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_agent import create_blog_discovery_agent, create_team_discovery_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict[str, Any]:
    """Load and return the domain YAML config.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        Parsed config as a dict.

    Raises:
        FileNotFoundError: If the config file does not exist.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_registry(output_path: Path) -> dict[str, Any]:
    """Load existing blog registry from JSON, or return empty dict.

    Args:
        output_path: Path to the registry JSON file.

    Returns:
        Dict mapping team names to their blog list + metadata.
    """
    if output_path.exists():
        return json.loads(output_path.read_text(encoding="utf-8"))
    return {}


def save_registry(registry: dict[str, Any], output_path: Path) -> None:
    """Save the blog registry to JSON.

    Args:
        registry: The full registry dict to persist.
        output_path: Path to write the JSON file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registry, indent=2, default=str), encoding="utf-8")
    logger.info("Registry saved to %s", output_path)


def discover_teams(config: dict[str, Any]) -> list[dict[str, str]]:
    """Run the team discovery agent to get all teams for the sport.

    Args:
        config: The domain config dict.

    Returns:
        List of dicts with "team" and "conference" keys.
    """
    sport = config["display_name"]
    primary_url = config["team_discovery"].get("primary_url", "")

    logger.info("Discovering teams for: %s", sport)
    agent = create_team_discovery_agent()

    prompt = f"Find all {sport} teams by conference."
    if primary_url:
        prompt += f" Start by fetching this URL: {primary_url}"

    result = agent(prompt)

    # Parse the agent's JSON response.
    response_text = str(result)
    # Extract JSON array from the response (agent may wrap it in markdown).
    start = response_text.find("[")
    end = response_text.rfind("]") + 1
    if start == -1 or end == 0:
        logger.error("Team discovery agent did not return valid JSON: %s", response_text[:500])
        return []

    teams = json.loads(response_text[start:end])
    logger.info("Discovered %d teams", len(teams))
    return teams


def _print_tool_trace(agent: Any, team: str) -> None:
    """Print a summary of tool calls made by the agent for debugging.

    Shows web search queries, access_check results, and recency_check results.

    Args:
        agent: The Strands agent (with .messages attribute).
        team: Team name for labeling the trace.
    """
    print(f"\n{'='*60}")
    print(f"  TOOL TRACE: {team}")
    print(f"{'='*60}")
    call_num = 0
    for msg in agent.messages:
        for content in msg.get("content", []):
            if "toolUse" in content:
                call_num += 1
                tool = content["toolUse"]
                name = tool.get("name", "")
                inp = tool.get("input", {})
                if "WebSearch" in name:
                    print(f"  [{call_num}] WEB SEARCH: {inp.get('query', '')}")
                elif "access_check" in name:
                    print(f"  [{call_num}] ACCESS CHECK: {inp.get('url', '')}")
                elif "recency_check" in name:
                    print(f"  [{call_num}] RECENCY CHECK: {inp.get('url', '')}")
                elif "http_fetch" in name:
                    print(f"  [{call_num}] HTTP FETCH: {inp.get('url', '')}")
            if "toolResult" in content:
                tr = content["toolResult"]
                result_content = tr.get("content", [])
                if isinstance(result_content, list):
                    for item in result_content:
                        if isinstance(item, dict) and "text" in item:
                            text = item["text"]
                            # Compact output for access/recency checks
                            try:
                                data = json.loads(text)
                                if "status_label" in data:
                                    print(f"           → {data['status_label']} (status={data.get('status_code')})")
                                elif "recency_status" in data:
                                    print(f"           → {data['recency_status']} (last_post={data.get('last_post_date')})")
                                elif "results" in data:
                                    urls = [r.get("url", "")[:80] for r in data.get("results", [])[:5]]
                                    print(f"           → {len(data.get('results',[]))} results: {urls}")
                            except (json.JSONDecodeError, TypeError):
                                pass
    print(f"{'='*60}\n")


def discover_blogs_for_team(
    team: str, sport: str, existing_blogs: list[dict], max_blogs: int, config: dict
) -> list[dict[str, Any]]:
    """Run the blog discovery agent for a single team.

    Args:
        team: Team name (e.g. "Duke").
        sport: Display name of the sport (e.g. "NCAA Men's Basketball").
        existing_blogs: List of existing blog entries for this team (may be empty).
        max_blogs: Max blogs to return.

    Returns:
        List of blog dicts with url, platform, accessible, status_label,
        recency_status, last_post_date.
    """
    agent = create_blog_discovery_agent(config=config, max_blogs=max_blogs)

    existing_context = ""
    if existing_blogs:
        urls = [b["url"] for b in existing_blogs]
        existing_context = (
            f"\n\nThis team already has these known blogs in the registry: {urls}. "
            "Re-validate them (check access + recency) and search for any new ones. "
            "Do not duplicate URLs already in the list."
        )

    prompt = (
        f"Find dedicated fan blogs and forums for {team} ({sport}).{existing_context}"
    )

    logger.info("Discovering blogs for: %s", team)
    result = agent(prompt)

    # In debug mode, print the tool call trace
    if logging.getLogger("tools.access_check").isEnabledFor(logging.DEBUG):
        _print_tool_trace(agent, team)

    # Parse the agent's JSON response.
    response_text = str(result)
    start = response_text.find("[")
    end = response_text.rfind("]") + 1
    if start == -1 or end == 0:
        logger.warning("Blog discovery for %s did not return valid JSON: %s", team, response_text[:500])
        return existing_blogs  # Keep existing if agent fails

    blogs = json.loads(response_text[start:end])
    # Remove 404s — these are likely hallucinated URLs
    blogs = [b for b in blogs if "404" not in str(b.get("status_label", ""))]
    return blogs


def _registry_to_excel(registry: dict[str, Any], output_path: Path) -> None:
    """Convert the registry JSON to an Excel file with one row per blog URL.

    Args:
        registry: The full registry dict.
        output_path: Path to write the .xlsx file.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Blog Registry"

    headers = ["Team", "Conference", "Sport", "URL", "Platform", "Accessible",
               "Status Label", "Recency Status", "Last Post Date", "RSS URL"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for entry in registry.values():
        team = entry.get("team", "")
        conference = entry.get("conference", "")
        sport = entry.get("sport", "")
        blogs = entry.get("blogs", [])
        if not blogs:
            ws.append([team, conference, sport, "", "", "", "no_blogs_found", "", "", ""])
        else:
            for b in blogs:
                ws.append([
                    team, conference, sport,
                    b.get("url", ""),
                    b.get("platform", ""),
                    b.get("accessible", ""),
                    b.get("status_label", ""),
                    b.get("recency_status", ""),
                    b.get("last_post_date", ""),
                    b.get("rss_url", ""),
                ])

    # Auto-size columns
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

    excel_path = output_path.with_suffix(".xlsx")
    wb.save(excel_path)
    logger.info("Excel saved to %s", excel_path)


def _print_summary(registry: dict[str, Any]) -> None:
    """Print a summary of the discovery results.

    Args:
        registry: The full registry dict.
    """
    from collections import Counter

    total = len(registry)
    if total == 0:
        return

    with_accessible = 0
    without_accessible = 0
    error_counter: Counter = Counter()

    for entry in registry.values():
        blogs = entry.get("blogs", [])
        if any(b.get("accessible") for b in blogs):
            with_accessible += 1
        else:
            without_accessible += 1
            for b in blogs:
                error_counter[b.get("status_label", "unknown")] += 1
            if not blogs:
                error_counter["no_blogs_found"] += 1

    print(f"\n{'='*50}")
    print(f"  DISCOVERY SUMMARY")
    print(f"{'='*50}")
    print(f"  Total teams:                  {total}")
    print(f"  With ≥1 accessible URL:       {with_accessible} ({with_accessible*100//total}%)")
    print(f"  With NO accessible URL:       {without_accessible} ({without_accessible*100//total}%)")

    if error_counter:
        total_errors = sum(error_counter.values())
        print(f"\n  Access errors (teams without working URLs):")
        for error, count in error_counter.most_common(10):
            print(f"    {error:20s}  {count:4d}  ({count*100//total_errors}%)")
    print(f"{'='*50}\n")


def main() -> None:
    """Run the full discovery pipeline."""
    parser = argparse.ArgumentParser(description="Blog Discovery Pipeline")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to domain config YAML (e.g. discovery/config/ncaa_mbb.yaml)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of teams to process (0 = all). Useful for testing.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging to see web search queries, tool outputs, and agent reasoning.",
    )
    args = parser.parse_args()

    # Set log level based on --debug flag
    if args.debug:
        logging.getLogger().setLevel(logging.INFO)
        # Show our tool logs at DEBUG
        logging.getLogger("tools.access_check").setLevel(logging.DEBUG)
        logging.getLogger("tools.recency_check").setLevel(logging.DEBUG)
        logging.getLogger("tools.http_fetch").setLevel(logging.DEBUG)
        # Suppress noisy libraries
        for name in ["botocore", "urllib3", "httpx", "httpcore", "mcp",
                     "opentelemetry", "strands", "asyncio"]:
            logging.getLogger(name).setLevel(logging.WARNING)
    else:
        logging.getLogger().setLevel(logging.INFO)
        for name in ["botocore", "urllib3", "httpx", "httpcore", "mcp",
                     "opentelemetry", "strands", "asyncio"]:
            logging.getLogger(name).setLevel(logging.WARNING)

    config = load_config(args.config)
    domain_id = config["domain_id"]
    sport = config["display_name"]
    max_blogs = config["blog_discovery"].get("max_blogs_per_team", 5)

    output_path = Path(__file__).resolve().parent / "output" / f"{domain_id}_registry.json"
    registry = load_registry(output_path)

    # Step 1: Discover teams
    teams = discover_teams(config)
    if args.limit > 0:
        teams = teams[: args.limit]
        logger.info("Limited to %d teams for testing", args.limit)

    # Step 2: Per team, discover/validate blogs
    for i, team_info in enumerate(teams, 1):
        team_name = team_info["team"]
        conference = team_info.get("conference", "Unknown")
        logger.info("[%d/%d] Processing: %s (%s)", i, len(teams), team_name, conference)

        existing_blogs = registry.get(team_name, {}).get("blogs", [])
        blogs = discover_blogs_for_team(team_name, sport, existing_blogs, max_blogs, config)

        registry[team_name] = {
            "sport": domain_id,
            "team": team_name,
            "conference": conference,
            "blogs": blogs,
        }

        # Save after each team (resume-friendly)
        save_registry(registry, output_path)

    logger.info("Discovery complete. %d teams processed.", len(teams))
    _registry_to_excel(registry, output_path)
    _print_summary(registry)


if __name__ == "__main__":
    main()
