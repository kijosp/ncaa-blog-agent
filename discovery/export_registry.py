"""Export the blog discovery registry from DynamoDB to a JSON file.

Scans the full `blog-discovery-registry` table and writes all items as a JSON
list, one entry per team. Each entry includes:
  - sport, team, team_id, conference
  - blogs: list of URL objects with accessibility, recency, bookmark status

DynamoDB table structure:
  - Partition key: "team" (string, e.g. "Colgate Raiders")
  - No sort key (single-key table)

Usage:
    AWS_PROFILE=fanduel uv run python export_registry.py
    AWS_PROFILE=fanduel uv run python export_registry.py --output my_export.json
"""

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TABLE_NAME = os.environ.get("REGISTRY_TABLE", "blog-discovery-registry")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _convert_decimals(obj):
    """Convert DynamoDB Decimal types to Python int/float for JSON serialization."""
    if isinstance(obj, list):
        return [_convert_decimals(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


def export_registry(output_path: str | None = None) -> list[dict]:
    """Scan the full DynamoDB registry and return all items as a list.

    Each item is a team-level dict containing:
      - team: team name (partition key)
      - team_id: normalized ID (e.g. "colgatraiders")
      - sport: sport name (e.g. "NCAA Men's Basketball")
      - conference: conference name
      - blogs: list of blog dicts, each with:
          - url, rss_url, accessible, recency_status,
            last_post_date, platform, bookmarked, bookmarked_at

    Returns:
        List of team dicts sorted by team name.
    """
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile, region_name=REGION)
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)

    items = []
    response = table.scan()
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    items = [_convert_decimals(item) for item in items]
    items.sort(key=lambda x: x.get("team", ""))

    print(f"Exported {len(items)} teams from {TABLE_NAME}")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(items, f, indent=2, default=str)
        print(f"Written to: {output_path}")

    return items


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export blog registry from DynamoDB to JSON")
    parser.add_argument(
        "--output", "-o",
        default=f"output/registry_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
        help="Output file path (default: output/registry_export_<timestamp>.json)",
    )
    args = parser.parse_args()

    items = export_registry(args.output)

    # Print summary
    sports = set(item.get("sport", "unknown") for item in items)
    total_urls = sum(len(item.get("blogs", [])) for item in items)
    bookmarked = sum(
        1 for item in items
        for blog in item.get("blogs", [])
        if blog.get("bookmarked")
    )
    accessible = sum(
        1 for item in items
        for blog in item.get("blogs", [])
        if blog.get("accessible")
    )

    print(f"\nSummary:")
    print(f"  Sports: {', '.join(sorted(sports))}")
    print(f"  Teams: {len(items)}")
    print(f"  Total URLs: {total_urls}")
    print(f"  Accessible: {accessible}")
    print(f"  Bookmarked: {bookmarked}")
