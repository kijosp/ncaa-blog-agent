#!/usr/bin/env python3
"""Orchestration script for the blog discovery pipeline.

Flow:
  1. Load domain config (YAML)
  2. Run team discovery agent → get all teams by conference
  3. Load existing registry (if any)
  4. Per team: run blog discovery agent → find/validate blogs
  5. Save updated registry as JSON

Usage:
cd <rootdir>/discovery
    To run on all teams in DEBUG mode (shows tool traces): AWS_PROFILE=FD uv run python run_discovery.py --config config/ncaa_mbb.yaml --workers 5 --debug > output/debug_run.log 2>&1 
    To run on N teams: AWS_PROFILE=FD uv run python run_discovery.py --config config/ncaa_mbb.yaml --limit N
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# Add discovery directory to path for imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_agent import create_blog_discovery_agent, create_team_discovery_agent, create_verifier_agent

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


def _get_registry_table():
    """Get DynamoDB Table resource for the blog registry."""
    import boto3
    table_name = os.environ.get("REGISTRY_TABLE", "blog-discovery-registry")
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return dynamodb.Table(table_name)


def generate_team_id(team_name: str) -> str:
    """Generate a stable team ID from a team name.

    Lowercase, remove spaces and commas. This produces a deterministic ID
    regardless of formatting variations.

    Examples:
        "Arizona Wildcats" → "arizonawildcats"
        "Central Connecticut Blue Devils" → "centralconnecticutbluedevils"
        "Texas A&M Aggies" → "texasa&maggies"

    Args:
        team_name: Full team name with mascot.

    Returns:
        Normalized team ID string.
    """
    return team_name.lower().replace(" ", "").replace(",", "")


def load_registry() -> dict[str, Any]:
    """Load full blog registry from DynamoDB.

    Returns:
        Dict mapping team names to their blog list + metadata.
    """
    table = _get_registry_table()
    registry = {}
    response = table.scan()
    for item in response.get("Items", []):
        registry[item["team"]] = item
    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        for item in response.get("Items", []):
            registry[item["team"]] = item
    return registry


def build_team_id_index(registry: dict) -> dict[str, str]:
    """Build a mapping of team_id → team_name from the registry.

    Used to match newly discovered teams against existing entries regardless
    of name formatting differences.

    Args:
        registry: Full registry dict keyed by team name.

    Returns:
        Dict mapping team_id to canonical team_name in the registry.
    """
    return {generate_team_id(name): name for name in registry.keys()}


def save_team_to_registry(team_name: str, entry: dict[str, Any]) -> None:
    """Save a single team entry to DynamoDB.

    Args:
        team_name: Team name (partition key).
        entry: Dict with sport, team, conference, blogs.
    """
    table = _get_registry_table()
    item = {**entry, "team": team_name, "team_id": generate_team_id(team_name)}
    # Convert any None values to empty strings for DynamoDB
    for blog in item.get("blogs", []):
        for k, v in list(blog.items()):
            if v is None:
                blog[k] = ""
    table.put_item(Item=item)
    logger.info("Saved to DynamoDB: %s (id: %s)", team_name, generate_team_id(team_name))


def _parse_json_array(response_text: str) -> list | None:
    """Try multiple strategies to extract a JSON array from LLM response text.

    Returns:
        Parsed list if successful, None if all strategies fail.
    """
    # Strategy 1: Try parsing the entire response as JSON
    try:
        result = json.loads(response_text)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: Strip markdown code fences
    json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response_text, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group(1))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find the longest valid [...] substring
    first_bracket = response_text.find("[")
    if first_bracket != -1:
        search_from = len(response_text)
        while search_from > first_bracket:
            end = response_text.rfind("]", first_bracket, search_from)
            if end == -1:
                break
            try:
                result = json.loads(response_text[first_bracket:end + 1])
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                search_from = end

    return None


def discover_teams(config: dict[str, Any], debug: bool = False) -> list[dict[str, str]]:
    """Run the team discovery agent to get all teams for the sport.

    Args:
        config: The domain config dict.

    Returns:
        List of dicts with "team" and "conference" keys.
    """
    sport = config["display_name"]
    primary_url = config["team_discovery"].get("primary_url", "")

    logger.info("Discovering teams for: %s", sport)
    agent = create_team_discovery_agent(config=config, debug=debug)

    prompt = f"Find all {sport} teams by conference."
    if primary_url:
        prompt += f" Start by fetching this URL: {primary_url}"

    result = agent(prompt)

    response_text = str(result)
    teams = _parse_json_array(response_text)

    # Retry: use a plain LLM call to extract JSON from the raw response
    if teams is None:
        logger.warning("Team discovery: parse failed, using LLM to extract JSON...")
        from strands.models import BedrockModel
        model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", temperature=0.0)
        extraction_agent = Agent(
            model=model,
            system_prompt="Extract the JSON array from the following text. Return ONLY the raw JSON array, nothing else.",
            callback_handler=null_callback_handler,
        )
        retry_result = extraction_agent(response_text[:50000])  # Truncate to avoid token limits
        teams = _parse_json_array(str(retry_result))

    if teams is None:
        logger.error("Team discovery: all parse attempts failed. Response: %s", response_text[:500])
        return []

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
    team: str, sport: str, existing_blogs: list[dict], max_blogs: int, config: dict, debug: bool = False
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
    agent = create_blog_discovery_agent(config=config, max_blogs=max_blogs, debug=debug)

    existing_context = ""
    if existing_blogs:
        urls = [b["url"] for b in existing_blogs]
        existing_context = (
            f"\n\nThis team already has these known blogs in the registry: {urls}. "
            "Re-validate them (check access + recency) and search for any new ones. "
            "Do not duplicate URLs already in the list."
        )

    query_template = config["blog_discovery"]["query"]
    prompt = query_template.format(team=team, sport=sport) + existing_context

    logger.info("Discovering blogs for: %s", team)
    result = agent(prompt)

    # In debug mode, print the tool call trace
    if logging.getLogger("tools.access_check").isEnabledFor(logging.DEBUG):
        _print_tool_trace(agent, team)

    # Parse JSON array from agent response.
    response_text = str(result)
    logger.debug("Raw agent response for %s (first 500 chars): %s", team, response_text[:500])

    blogs = _parse_json_array(response_text)

    # Retry: use a plain LLM call to extract JSON from the raw response
    if blogs is None:
        logger.warning("Blog discovery for %s: parse failed, using LLM to extract JSON...", team)
        from strands.models import BedrockModel
        model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", temperature=0.0)
        extraction_agent = Agent(
            model=model,
            system_prompt="Extract the JSON array from the following text. Return ONLY the raw JSON array, nothing else.",
            callback_handler=null_callback_handler,
        )
        retry_result = extraction_agent(response_text[:50000])
        blogs = _parse_json_array(str(retry_result))

    if blogs is None:
        logger.warning("Blog discovery for %s: all parse attempts failed", team)
        return existing_blogs
    # Remove 404s — these are likely hallucinated URLs
    blogs = [b for b in blogs if "404" not in str(b.get("status_label", ""))]
    return blogs


def verify_blogs(team: str, blogs: list[dict], config: dict, debug: bool = False) -> list[dict]:
    """Verify that discovered blog URLs are specifically for US Men's College Basketball.

    Args:
        team: Team name.
        blogs: List of blog dicts from discovery.
        config: Domain config dict.
        debug: If True, stream agent output.

    Returns:
        Filtered list keeping only URLs that pass verification.
    """
    if not blogs:
        return blogs

    agent = create_verifier_agent(config=config, debug=debug)
    urls = [b.get("url", "") for b in blogs]
    prompt = (
        f"Verify these URLs for {team} (US Men's College Basketball). "
        f"For each URL, determine if it is specifically for this team's men's college basketball program.\n"
        f"URLs to verify: {json.dumps(urls)}"
    )

    logger.info("Verifying %d URLs for: %s", len(urls), team)
    result = agent(prompt)

    # Parse the verdict JSON
    response_text = str(result)
    end = response_text.rfind("]") + 1
    start = response_text.rfind("[", 0, end)
    if start == -1 or end == 0:
        logger.warning("Verifier for %s did not return valid JSON, keeping all URLs", team)
        return blogs

    try:
        verdicts = json.loads(response_text[start:end])
    except json.JSONDecodeError as e:
        logger.warning("Verifier for %s: JSON parse error: %s, keeping all URLs", team, e)
        return blogs

    # Build set of URLs that passed
    passed_urls = {v.get("url") for v in verdicts if v.get("verdict") == "PASS"}
    failed = [v for v in verdicts if v.get("verdict") == "FAIL"]
    for f in failed:
        logger.info("  REJECTED: %s — %s", f.get("url"), f.get("reason"))

    return [b for b in blogs if b.get("url") in passed_urls]


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

    excel_path = output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
    with_accessible_and_active = 0
    without_accessible = 0
    error_counter: Counter = Counter()

    for entry in registry.values():
        blogs = entry.get("blogs", [])
        if any(b.get("accessible") for b in blogs):
            with_accessible += 1
            if any(b.get("accessible") and b.get("recency_status") == "active" for b in blogs):
                with_accessible_and_active += 1
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
    print(f"  With ≥1 accessible + active:  {with_accessible_and_active} ({with_accessible_and_active*100//total}%)")
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
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers for blog discovery (default: 1, sequential).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging to see web search queries, tool outputs, and agent reasoning.",
    )
    parser.add_argument(
        "--team",
        type=str,
        default="",
        help="Run discovery for a single team only (e.g. 'Colgate Raiders'). Case-insensitive partial match.",
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

    registry = load_registry()
    team_id_index = build_team_id_index(registry)

    # Step 1: Discover teams (skip if --team is specified)
    if args.team:
        # Look up team in existing registry by team_id first
        team_id = generate_team_id(args.team)
        canonical_name = team_id_index.get(team_id)
        if canonical_name:
            existing_entry = registry[canonical_name]
        else:
            # Fallback: try case-insensitive partial match on registry keys
            existing_entry = {}
            for key, val in registry.items():
                if args.team.lower() in key.lower():
                    existing_entry = val
                    break
        team_name = existing_entry.get("team", args.team)
        conference = existing_entry.get("conference", "Unknown")
        teams = [{"team": team_name, "conference": conference}]
        logger.info("Skipping team discovery — running for: %s (%s)", team_name, conference)
    else:
        teams = discover_teams(config, debug=args.debug)

    if args.limit > 0:
        teams = teams[: args.limit]
        logger.info("Limited to %d teams for testing", args.limit)

    # Step 2: Per team, discover/validate blogs
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def process_team(team_info: dict, index: int) -> None:
        team_name = team_info["team"]
        conference = team_info.get("conference", "Unknown")
        team_id = generate_team_id(team_name)

        # Resolve against existing registry by team_id
        # e.g. "Arizona  Wildcats" and "arizona wildcats" both produce ID "arizonawildcats"
        canonical_name = team_id_index.get(team_id)
        if canonical_name and canonical_name != team_name:
            logger.info("[%d/%d] Normalized: '%s' → '%s' (id: %s)", index, len(teams), team_name, canonical_name, team_id)
            team_name = canonical_name
        else:
            logger.info("[%d/%d] Processing: %s (%s) [id: %s]", index, len(teams), team_name, conference, team_id)

        existing_blogs = registry.get(team_name, {}).get("blogs", [])
        existing_urls = {b.get("url") for b in existing_blogs}

        # Always run discovery (may find new blogs)
        discovered = discover_blogs_for_team(team_name, sport, existing_blogs, max_blogs, config, debug=args.debug)

        # Split: new URLs go through full verification, existing just get recency refresh
        new_blogs = [b for b in discovered if b.get("url") not in existing_urls]
        if new_blogs and config.get("verifier"):
            new_blogs = verify_blogs(team_name, new_blogs, config, debug=args.debug)

        # Refresh recency for existing blogs (already verified, skip re-verification)
        for blog in existing_blogs:
            if blog.get("accessible"):
                from tools.recency_check import recency_check as _recency_check
                result = _recency_check._tool_func(url=blog["url"], rss_url=blog.get("rss_url", ""))
                blog["recency_status"] = result.get("recency_status", blog.get("recency_status"))
                blog["last_post_date"] = result.get("last_post_date", blog.get("last_post_date"))

        # Merge: existing (refreshed) + newly verified
        blogs = existing_blogs + new_blogs

        entry = {
            "sport": domain_id,
            "team": team_name,
            "conference": conference,
            "blogs": blogs,
        }
        save_team_to_registry(team_name, entry)

    workers = args.workers
    if workers <= 1:
        for i, team_info in enumerate(teams, 1):
            process_team(team_info, i)
    else:
        logger.info("Running with %d parallel workers", workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_team, team_info, i): team_info
                for i, team_info in enumerate(teams, 1)
            }
            for future in as_completed(futures):
                team_info = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("Error processing %s: %s", team_info["team"], e)

    logger.info("Discovery complete. %d teams processed.", len(teams))

    # Reload full registry from DynamoDB for excel export and summary
    final_registry = load_registry()
    output_path = Path(__file__).resolve().parent / "output" / f"{domain_id}_registry.xlsx"
    _registry_to_excel(final_registry, output_path)
    _print_summary(final_registry)


if __name__ == "__main__":
    main()
