# Model Comparison Evaluation

Compares event detection quality and cost across Bedrock model configurations for the blog search agent.

## Objective

The blog search agent has 3 LLM swap points:
- **Agent** (Strands orchestrator): reasons about which tools to call
- **RSS Extraction**: extracts events from RSS feed content
- **Web Extraction**: extracts events from web-fetched page content

This framework runs the full workflow with different model combinations and measures:
- **Event coverage** (recall vs. an Opus 4.6 baseline)
- **Cost breakdown** per component (agent LLM, RSS extraction LLM, web extraction LLM, web search API)
- **Token usage** and latency per team

## Setup

```bash
# From repo root
cp blog_search/.env_setup blog_search/.env
# Fill in: GATEWAY_URL, COGNITO_DOMAIN, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET, AWS_PROFILE
```

Install dependencies (if not using the existing venv):
```bash
pip install strands-agents mcp pandas matplotlib python-dotenv feedparser pydantic boto3 requests
```

## Verify Gateway

Before running evaluations, confirm the MCP gateway is serving the WebSearch tool:

```bash
python evaluation/test_tool_metrics.py
```

Look for:
```
MCP tools available: 1
  - WebSearch
```

If it shows `0 tools`, the gateway deployment needs to be fixed — models won't have web search available.

## Run

Open `evaluation/model_comparison.ipynb` in Jupyter and run cells sequentially. The notebook:

1. Samples teams from the blog registry
2. Runs each model config (Opus baseline, GLM 5, Haiku 4.5, Nova 2 Lite, current default mix)
3. Compares event coverage and cost

Results are saved to `evaluation/results/` (gitignored).

## Model Configurations

| Config | Agent | RSS Extraction | Web Extraction |
|--------|-------|----------------|----------------|
| Baseline | Opus 4.6 | Opus 4.6 | Opus 4.6 |
| GLM 5 | GLM 5 | GLM 5 | GLM 5 |
| Haiku 4.5 | Haiku 4.5 | Haiku 4.5 | Haiku 4.5 |
| Nova 2 Lite | Nova 2 Lite | Nova 2 Lite | Nova 2 Lite |
| Current Default | Sonnet 4.6 | Nova 2 Lite | Haiku 4.5 |

## Cost Components

- **Web Search API**: $7 per 1,000 queries (gateway\_\_WebSearch tool calls)
- **Agent LLM**: orchestrator reasoning + tool selection tokens
- **RSS Extraction LLM**: tokens for extracting events from RSS content
- **Web Extraction LLM**: tokens for extracting events from web-fetched pages

## File Structure

```
evaluation/
  model_comparison.ipynb    # Main notebook
  test_tool_metrics.py      # Gateway health check + metrics inspector
  scripts/
    runner.py               # Runs workflow with model config, captures tokens
    pricing.py              # Bedrock pricing constants + cost calculation
    comparator.py           # Event overlap / recall computation
    registry_sampler.py     # Random team sampling from registry
  results/                  # Output JSONs + charts (gitignored)
```
