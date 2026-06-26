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
from strands.handlers.callback_handler import null_callback_handler
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
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    return BedrockModel(model_id=model_id, temperature=0.1)


def create_team_discovery_agent(config: dict, debug: bool = False) -> Agent:
    """Create an agent that finds all teams for a sport using web search + http_fetch.

    Tools: WebSearch (via Gateway MCP), http_fetch

    Args:
        config: The domain config dict (must contain team_discovery.prompt).
        debug: If True, stream agent output to stdout.

    Returns:
        A Strands Agent configured for team discovery.
    """
    gateway_client = _create_gateway_mcp_client()
    prompt = config["team_discovery"]["prompt"]

    return Agent(
        name="team_discovery_agent",
        model=_get_model(),
        tools=[gateway_client, http_fetch],
        system_prompt=prompt,
        callback_handler=None if debug else null_callback_handler,
    )


def create_blog_discovery_agent(config: dict, max_blogs: int = 5, debug: bool = False) -> Agent:
    """Create an agent that finds and validates fan blogs for one team.

    Loads the system prompt from config["blog_discovery"]["prompt"].

    Tools: WebSearch (via Gateway MCP), access_check, recency_check

    Args:
        config: The domain config dict (must contain blog_discovery.prompt).
        max_blogs: Maximum number of blogs to return per team.
        debug: If True, stream agent output to stdout.

    Returns:
        A Strands Agent configured for blog discovery.
    """
    gateway_client = _create_gateway_mcp_client()
    from datetime import date
    reference_date = config.get("reference_date") or date.today().isoformat()
    reference_year = reference_date[:4]
    prompt = (
        config["blog_discovery"]["prompt"]
        .replace("{max_blogs}", str(max_blogs))
        .replace("{reference_date}", reference_date)
        .replace("{reference_year}", reference_year)
    )

    return Agent(
        name="blog_discovery_agent",
        model=_get_model(),
        tools=[gateway_client, access_check, recency_check],
        system_prompt=prompt,
        callback_handler=None if debug else null_callback_handler,
    )


def create_verifier_agent(config: dict, debug: bool = False) -> Agent:
    """Create an agent that verifies URLs are specifically for US Men's College Basketball.

    Tools: http_fetch (to inspect page content)

    Args:
        config: The domain config dict (must contain verifier.prompt).
        debug: If True, stream agent output to stdout.

    Returns:
        A Strands Agent configured for URL verification.
    """
    prompt = config["verifier"]["prompt"]
    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", temperature=0.0)

    return Agent(
        name="verifier_agent",
        model=model,
        tools=[http_fetch],
        system_prompt=prompt,
        callback_handler=None if debug else null_callback_handler,
    )
