"""Strands agents for the blog discovery pipeline.

Two agents with minimal, focused tool sets:
  - Team Discovery Agent: web_search only. Finds all teams for a sport.
  - Blog Discovery Agent: web_search + access_check + recency_check.
    Finds and validates fan blogs for a single team.

Both agents connect to the same AgentCore Gateway for the WebSearch tool.
"""

import logging
import os

import requests as http_requests
from dotenv import load_dotenv
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from tools.access_check import access_check
from tools.recency_check import recency_check
from tools.http_fetch import http_fetch

logger = logging.getLogger(__name__)

# Load .env from the discovery directory
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _get_m2m_token() -> str:
    """Fetch an M2M access token from Cognito using client_credentials grant.

    Reads COGNITO_DOMAIN, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET from env.

    Returns:
        A valid JWT access token string.

    Raises:
        ValueError: If required Cognito env vars are missing.
        RuntimeError: If the token request fails.
    """
    domain = os.environ.get("COGNITO_DOMAIN")
    client_id = os.environ.get("COGNITO_CLIENT_ID")
    client_secret = os.environ.get("COGNITO_CLIENT_SECRET")

    if not all([domain, client_id, client_secret]):
        raise ValueError(
            "COGNITO_DOMAIN, COGNITO_CLIENT_ID, and COGNITO_CLIENT_SECRET must be set"
        )

    resp = http_requests.post(
        f"https://{domain}/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in Cognito response: {resp.text}")
    return token


def _create_gateway_mcp_client() -> MCPClient:
    """Create an MCP client pointing to the AgentCore Gateway.

    Reads GATEWAY_URL from environment. Token is either from GATEWAY_TOKEN env var
    or auto-fetched from Cognito using client credentials.

    Returns:
        MCPClient configured with the Gateway's WebSearch tool.

    Raises:
        ValueError: If GATEWAY_URL is not set.
    """
    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        raise ValueError("GATEWAY_URL environment variable is required")

    # Use explicit token if set, otherwise fetch from Cognito
    gateway_token = os.environ.get("GATEWAY_TOKEN") or _get_m2m_token()

    return MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url,
            headers={"Authorization": f"Bearer {gateway_token}"},
        ),
        prefix="gateway",
    )


def _get_model() -> BedrockModel:
    """Create the Bedrock model for both agents.

    Returns:
        BedrockModel configured with Claude Sonnet.
    """
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    return BedrockModel(model_id=model_id, temperature=0.1)


_TEAM_DISCOVERY_PROMPT = """You are a sports research assistant. Your job is to find all teams
for a given sport and league.

Follow this order:
1. First, use http_fetch on the primary URL provided in the user's message.
2. If http_fetch fails or returns no usable content, use web_search to find a working
   ESPN page (e.g. search "men's basketball NCAA division 1 teams list ESPN"), then http_fetch that.
3. If ESPN still fails, fall back to Wikipedia (search for "List of NCAA Division I men's basketball programs").
4. Extract all team names and their conferences from the page content.
5. Return a JSON array of objects with "team" and "conference" fields.

Return ONLY valid JSON. No explanation text outside the JSON.
Format: [{"team": "Duke", "conference": "ACC"}, ...]"""


def create_team_discovery_agent() -> Agent:
    """Create an agent that finds all teams for a sport using web search + http_fetch.

    Tools: WebSearch (via Gateway MCP), http_fetch

    Returns:
        A Strands Agent configured for team discovery.
    """
    gateway_client = _create_gateway_mcp_client()

    return Agent(
        name="team_discovery_agent",
        model=_get_model(),
        tools=[gateway_client, http_fetch],
        system_prompt=_TEAM_DISCOVERY_PROMPT,
    )


def create_blog_discovery_agent(config: dict, max_blogs: int = 5) -> Agent:
    """Create an agent that finds and validates fan blogs for one team.

    Loads the system prompt from config["blog_discovery"]["prompt"].

    Tools: WebSearch (via Gateway MCP), access_check, recency_check

    Args:
        config: The domain config dict (must contain blog_discovery.prompt).
        max_blogs: Maximum number of blogs to return per team.

    Returns:
        A Strands Agent configured for blog discovery.
    """
    gateway_client = _create_gateway_mcp_client()
    prompt = config["blog_discovery"]["prompt"].replace("{max_blogs}", str(max_blogs))

    return Agent(
        name="blog_discovery_agent",
        model=_get_model(),
        tools=[gateway_client, access_check, recency_check],
        system_prompt=prompt,
    )
