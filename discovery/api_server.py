"""Simple API server for the blog discovery dashboard.

Reads from DynamoDB and serves registry data as JSON.
Run: uv run python api_server.py
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

import boto3
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TABLE_NAME = os.environ.get("REGISTRY_TABLE", "blog-discovery-registry")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
PORT = int(os.environ.get("API_PORT", "8080"))


def get_registry() -> dict:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)
    registry = {}
    response = table.scan()
    for item in response.get("Items", []):
        # Convert Decimal to int/float for JSON serialization
        registry[item["team"]] = _convert_decimals(item)
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        for item in response.get("Items", []):
            registry[item["team"]] = _convert_decimals(item)
    return registry


def _convert_decimals(obj):
    """Convert DynamoDB Decimal types to Python int/float."""
    from decimal import Decimal
    if isinstance(obj, list):
        return [_convert_decimals(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/registry":
            data = get_registry()
            self._json_response(data)
        elif self.path.startswith("/api/team/"):
            team_name = self.path[len("/api/team/"):].replace("%20", " ")
            data = get_registry()
            entry = data.get(team_name)
            if entry:
                self._json_response(entry)
            else:
                self._json_response({"error": "Team not found"}, 404)
        else:
            self._json_response({"error": "Not found"}, 404)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs


if __name__ == "__main__":
    print(f"API server running on http://localhost:{PORT}")
    print(f"  GET /api/registry — full registry")
    print(f"  GET /api/team/<name> — single team")
    HTTPServer(("", PORT), Handler).serve_forever()
