"""Test script: inspect what gateway__WebSearch actually returns.

Run with: AWS_PROFILE=fanduel uv run python test_web_search_output.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
for noisy in ["botocore", "urllib3", "httpcore", "httpx", "mcp.client"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _get_m2m_token() -> str:
    import requests

    domain = os.environ["COGNITO_DOMAIN"]
    client_id = os.environ["COGNITO_CLIENT_ID"]
    client_secret = os.environ["COGNITO_CLIENT_SECRET"]
    resp = requests.post(
        f"https://{domain}/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def main():
    gateway_url = os.environ["GATEWAY_URL"]
    token = os.environ.get("GATEWAY_TOKEN") or _get_m2m_token()

    client = MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            headers={"Authorization": f"Bearer {token}"},
        ),
        prefix="gateway",
    )

    # Test queries
    queries = [
        'NCAA men\'s basketball Colgate Raiders venue updates',
        'site:thecolgatemaroonnews.com injury 2026',
        'Duke Blue Devils men\'s basketball schedule change July 2026',
    ]

    async with client:
        # List available tools first
        tools = client.tool_names if hasattr(client, 'tool_names') else []
        print(f"Available gateway tools: {tools}\n")

        for query in queries:
            print(f"{'='*70}")
            print(f"QUERY: {query}")
            print(f"{'='*70}")

            # The MCP tool call — we need to invoke it through strands or directly
            # Since MCPClient wraps the MCP protocol, let's use the tool directly
            try:
                result = await client.call_tool(
                    "gateway_web-search-tool___WebSearch",
                    {"query": query, "maxResults": 5},
                )
                print(f"\nRaw result type: {type(result)}")
                print(f"\nRaw result:")
                print(json.dumps(result, indent=2, default=str))
            except Exception as e:
                print(f"Error: {e}")
                # Try alternate tool name
                try:
                    result = await client.call_tool(
                        "WebSearch",
                        {"query": query, "maxResults": 5},
                    )
                    print(f"\nRaw result (alt name):")
                    print(json.dumps(result, indent=2, default=str))
                except Exception as e2:
                    print(f"Alt name also failed: {e2}")

            print()


if __name__ == "__main__":
    asyncio.run(main())
