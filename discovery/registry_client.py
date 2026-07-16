"""Shared DynamoDB registry client.

Centralizes table access, key schema, and common operations for the
blog-discovery-registry table.

DynamoDB key schema:
  - Partition key (PK): team_id — normalized team name (lowercase, no spaces/commas)
  - Sort key (SK): sport — domain ID string (e.g. "ncaa_mbb")
"""

import os
from decimal import Decimal
from typing import Any

import boto3
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TABLE_NAME = os.environ.get("REGISTRY_TABLE", "blog-discovery-registry")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
DEFAULT_SPORT = os.environ.get("DEFAULT_SPORT", "ncaa_mbb")


def generate_team_id(team_name: str) -> str:
    """Generate a stable team ID from a team name.

    Lowercase, remove spaces and commas.

    Examples:
        "Arizona Wildcats" → "arizonawildcats"
        "Texas A&M Aggies" → "texasa&maggies"
    """
    return team_name.lower().replace(" ", "").replace(",", "")


def make_key(team_name: str, sport: str = "") -> dict[str, str]:
    """Build the DynamoDB composite key.

    Args:
        team_name: Human-readable team name (e.g. "Colgate Raiders").
        sport: Sport domain ID (e.g. "ncaa_mbb"). Defaults to DEFAULT_SPORT env var.

    Returns:
        Dict with team_id (PK) and sport (SK) suitable for Key= parameter.
    """
    return {"team_id": generate_team_id(team_name), "sport": sport or DEFAULT_SPORT}


def get_table():
    """Get the DynamoDB table resource."""
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile, region_name=REGION)
    dynamodb = session.resource("dynamodb")
    return dynamodb.Table(TABLE_NAME)


def convert_decimals(obj: Any) -> Any:
    """Convert DynamoDB Decimal types to Python int/float for JSON serialization."""
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


def scan_all() -> list[dict]:
    """Scan the full registry table and return all items."""
    table = get_table()
    items = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [convert_decimals(item) for item in items]


def get_item(team_name: str, sport: str = "") -> dict | None:
    """Get a single team item by name and sport."""
    table = get_table()
    response = table.get_item(Key=make_key(team_name, sport))
    item = response.get("Item")
    return convert_decimals(item) if item else None


def put_item(item: dict) -> None:
    """Write a team item to DynamoDB. Item must include team_id and sport."""
    table = get_table()
    for blog in item.get("blogs", []):
        for k, v in list(blog.items()):
            if v is None:
                blog[k] = ""
    table.put_item(Item=item)


def update_blogs(team_name: str, blogs: list[dict], sport: str = "") -> None:
    """Update only the blogs list for a team."""
    table = get_table()
    for blog in blogs:
        for k, v in list(blog.items()):
            if v is None:
                blog[k] = ""
    table.update_item(
        Key=make_key(team_name, sport),
        UpdateExpression="SET blogs = :b",
        ExpressionAttributeValues={":b": blogs},
    )
