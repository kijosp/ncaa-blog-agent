"""Registry reader — fetches team blog URLs from the DynamoDB discovery registry.

Reads the blog-discovery-registry table and returns structured data
about which URLs (and RSS feeds) are available for a given team.
"""

import logging
import os
from decimal import Decimal
from typing import Any

import boto3
from dotenv import load_dotenv
from strands import tool

logger = logging.getLogger(__name__)

# Load env
_this_dir = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_this_dir, "..", ".env")
if not os.path.exists(_env_path):
    _env_path = os.path.join(_this_dir, "..", "..", "discovery", ".env")
load_dotenv(_env_path)

TABLE_NAME = os.environ.get("REGISTRY_TABLE", "blog-discovery-registry")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _convert_decimals(obj: Any) -> Any:
    """Convert DynamoDB Decimal types to Python int/float."""
    if isinstance(obj, list):
        return [_convert_decimals(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


def _get_table():
    """Get the DynamoDB table resource."""
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile, region_name=REGION)
    dynamodb = session.resource("dynamodb")
    return dynamodb.Table(TABLE_NAME)


def get_team_urls(team_name: str) -> dict[str, Any]:
    """Fetch all discovered blog URLs for a team from the registry.

    Args:
        team_name: The team name as stored in DynamoDB (e.g., "Colgate Raiders").

    Returns:
        A dict with:
          - team: team name
          - blogs: list of blog dicts, each containing:
              - url: the blog URL
              - rss_url: RSS feed URL (empty string if none)
              - accessible: whether the URL was reachable
              - recency_status: "active", "outdated", or "unknown"
              - last_post_date: last known post date
              - platform: detected platform type
          - total_urls: count of all URLs
          - accessible_urls: count of accessible, non-outdated URLs (searchable)
          - urls_with_rss: count of URLs that have RSS feeds
          - error: str or None
    """
    try:
        table = _get_table()
        response = table.get_item(Key={"team": team_name})
        item = response.get("Item")

        if not item:
            logger.warning("[REGISTRY] Team not found: %s", team_name)
            return {
                "team": team_name,
                "blogs": [],
                "total_urls": 0,
                "searchable_urls": 0,
                "urls_with_rss": 0,
                "error": f"Team '{team_name}' not found in registry",
            }

        item = _convert_decimals(item)
        blogs = item.get("blogs", [])

        # Filter: must be accessible AND not outdated
        # accessible=True means the URL was reachable (not bot-blocked)
        # recency_status="outdated" means last post > 365 days ago — skip these
        searchable_blogs = [
            b for b in blogs
            if b.get("accessible", False)
            and b.get("recency_status") != "outdated"
        ]
        blogs_with_rss = [b for b in searchable_blogs if b.get("rss_url")]

        logger.info(
            "[REGISTRY] team=%s total=%d searchable=%d (accessible + not outdated) with_rss=%d",
            team_name, len(blogs), len(searchable_blogs), len(blogs_with_rss),
        )

        return {
            "team": team_name,
            "blogs": searchable_blogs,
            "total_urls": len(blogs),
            "searchable_urls": len(searchable_blogs),
            "urls_with_rss": len(blogs_with_rss),
            "error": None,
        }

    except Exception as e:
        logger.error("[REGISTRY] Error fetching team=%s: %s", team_name, e)
        return {
            "team": team_name,
            "blogs": [],
            "total_urls": 0,
            "searchable_urls": 0,
            "urls_with_rss": 0,
            "error": str(e),
        }


@tool
def registry_lookup(team_name: str) -> dict[str, Any]:
    """Look up a team's discovered fan blog URLs from the DynamoDB registry.

    Use this tool when a user asks about a team but does not provide specific URLs.
    Returns the team's searchable blog URLs (accessible and recently active) along
    with their RSS feed URLs if available.

    Args:
        team_name: The team name (e.g., "Colgate Raiders", "Duke Blue Devils").

    Returns:
        A dict with team name, list of blog dicts (url, rss_url), and counts.
    """
    return get_team_urls(team_name)


def get_all_teams() -> list[str]:
    """List all team names in the registry.

    Returns:
        Sorted list of team name strings.
    """
    try:
        table = _get_table()
        teams = []
        response = table.scan(ProjectionExpression="team")
        for item in response.get("Items", []):
            teams.append(item["team"])
        while "LastEvaluatedKey" in response:
            response = table.scan(
                ProjectionExpression="team",
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            for item in response.get("Items", []):
                teams.append(item["team"])
        return sorted(teams)
    except Exception as e:
        logger.error("[REGISTRY] Error listing teams: %s", e)
        return []
