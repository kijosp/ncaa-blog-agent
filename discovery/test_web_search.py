"""Quick test: Can the AgentCore web search tool find content from a specific URL?"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_agent import _create_gateway_mcp_client
from strands import Agent
from strands.models import BedrockModel

gateway_client = _create_gateway_mcp_client()

agent = Agent(
    model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-6", temperature=0.0),
    tools=[gateway_client],
    system_prompt="You are a research assistant. Use web_search to answer questions.",
)

result = agent(
    'Search for injury news from site:crossports.freeforums.net for men\'s basketball. '
    'Also try searching "crossports freeforums mens basketball injury". '
    'Report what you find — did the search return any results from that site?'
)

print("\n\n=== RESULT ===")
print(result)
