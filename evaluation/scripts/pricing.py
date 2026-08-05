"""Bedrock model pricing constants (us-east-1, on-demand, per 1K tokens)."""

WEB_SEARCH_COST_PER_QUERY = 0.007  # $7 per 1,000 queries

# Source prices are per 1M tokens; divided by 1000 for per-1K rates.
PRICING = {
    "us.anthropic.claude-opus-4-6-v1": {"input_per_1k": 0.005, "output_per_1k": 0.025},
    "us.anthropic.claude-sonnet-4-6": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"input_per_1k": 0.001, "output_per_1k": 0.005},
    "us.amazon.nova-2-lite-v1:0": {"input_per_1k": 0.0003, "output_per_1k": 0.0025},
    "zai.glm-5": {"input_per_1k": 0.001, "output_per_1k": 0.0032},
}

MODEL_CONFIGS = {
    "baseline_opus": {
        "label": "Claude Opus 4.6",
        "model_id": "us.anthropic.claude-opus-4-6-v1",
        "rss_model_id": "us.anthropic.claude-opus-4-6-v1",
        "web_model_id": "us.anthropic.claude-opus-4-6-v1",
    },
    "challenger_glm": {
        "label": "GLM 5",
        "model_id": "zai.glm-5",
        "rss_model_id": "zai.glm-5",
        "web_model_id": "zai.glm-5",
    },
    "challenger_haiku": {
        "label": "Haiku 4.5",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "rss_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "web_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    },
    "challenger_nova": {
        "label": "Nova 2 Lite",
        "model_id": "us.amazon.nova-2-lite-v1:0",
        "rss_model_id": "us.amazon.nova-2-lite-v1:0",
        "web_model_id": "us.amazon.nova-2-lite-v1:0",
    },
    "current_default": {
        "label": "Sonnet 4.6 + Nova Lite + Haiku 4.5",
        "model_id": "us.anthropic.claude-sonnet-4-6",
        "rss_model_id": "us.amazon.nova-2-lite-v1:0",
        "web_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    },
}


def compute_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Compute cost in USD for a given model and token counts."""
    pricing = PRICING.get(model_id, {"input_per_1k": 0, "output_per_1k": 0})
    return (
        (input_tokens / 1000) * pricing["input_per_1k"]
        + (output_tokens / 1000) * pricing["output_per_1k"]
    )


def compute_cost_breakdown(
    model_config: dict, token_usage: dict, web_searches: int = 0
) -> dict:
    """Return itemized cost breakdown by component."""
    agent_cost = compute_cost(
        model_config["model_id"],
        token_usage.get("agent_input_tokens", 0),
        token_usage.get("agent_output_tokens", 0),
    )
    rss_cost = compute_cost(
        model_config["rss_model_id"],
        token_usage.get("rss_input_tokens", 0),
        token_usage.get("rss_output_tokens", 0),
    )
    web_extraction_cost = compute_cost(
        model_config["web_model_id"],
        token_usage.get("web_input_tokens", 0),
        token_usage.get("web_output_tokens", 0),
    )
    search_cost = web_searches * WEB_SEARCH_COST_PER_QUERY
    llm_cost = agent_cost + rss_cost + web_extraction_cost
    total = llm_cost + search_cost
    return {
        "agent_llm": agent_cost,
        "rss_llm": rss_cost,
        "web_llm": web_extraction_cost,
        "web_search": search_cost,
        "total_llm": llm_cost,
        "total": total,
    }


def compute_run_cost(
    model_config: dict, token_usage: dict, web_searches: int = 0
) -> float:
    """Compute total cost for a run given model config, token usage, and web searches."""
    return compute_cost_breakdown(model_config, token_usage, web_searches)["total"]
