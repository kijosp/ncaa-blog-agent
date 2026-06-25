# Web Search Tool Cost & Rate Limit Analysis

**Date:** June 23, 2026  
**Purpose:** Evaluate web search APIs for the Blog Discovery Pipeline  
**Use Case:** Discover fan blogs/forums for ~360 NCAA teams (weekly cadence)

---

## Tools Compared

1. **Amazon Bedrock AgentCore Web Search** — Managed MCP connector on AgentCore Gateway
2. **Tavily Search API** — LLM-optimized search engine for AI agents
3. **DuckDuckGo** — Privacy-focused search engine (no official programmatic API)

---

## Cost Comparison

| Metric | AgentCore Web Search | Tavily | DuckDuckGo |
|--------|---------------------|--------|------------|
| Cost per 1,000 queries | $7.00 | $8.00 (Pay-As-You-Go at $0.008/credit) | Free (no official API) |
| Free tier | $200 AWS Free Tier credits for new accounts | 1,000 credits/month free | N/A |
| Billing model | Usage-based, no upfront | Credit-based or Pay-As-You-Go | N/A |

**Sources:**
- AgentCore pricing: https://aws.amazon.com/bedrock/agentcore/pricing/
- AgentCore blog (pricing confirmation): https://aws.amazon.com/blogs/aws/announcing-web-search-on-amazon-bedrock-agentcore-ground-your-ai-agents-in-current-accurate-web-knowledge/
- Tavily pricing page: https://tavily.com/pricing
- Tavily credits & pricing docs: https://docs.tavily.com/documentation/api-credits

---

## Rate Limits

| Metric | AgentCore Web Search | Tavily | DuckDuckGo |
|--------|---------------------|--------|------------|
| Default rate limit | 10 transactions per second (adjustable via Service Quotas) | 100 RPM (dev) / 1,000 RPM (prod) | No official API; unofficial use gets 429 rate-limited |
| Can increase? | Yes (AWS Service Quotas) | Enterprise plan (custom) | No |
| Max results per query | 25 | 20 | N/A |

**Sources:**
- AgentCore Gateway quotas ("Rate of Web Search Tool requests: 10 TPS"): https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html
- Tavily rate limits: https://docs.tavily.com/guides/rate-limits
- DuckDuckGo rate limiting (community report): https://github.com/open-webui/open-webui/discussions/6624

---

## Index Freshness & Quality

| Metric | AgentCore Web Search | Tavily | DuckDuckGo |
|--------|---------------------|--------|------------|
| Search source | Amazon's purpose-built web index + Amazon Knowledge Graph | Tavily's proprietary index | DuckDuckGo index (Bing-based) |
| Freshness claim | "refreshed within minutes" (per AWS blog); "tens of billions of documents" | Not publicly disclosed | Not disclosed |
| LLM-optimized output | Yes — semantic snippets, URLs, titles, publication dates | Yes — optimized for LLM consumption | No — raw search results |
| Result format | Structured JSON via MCP (snippets + URLs + dates) | Structured JSON (snippets + URLs + scores) | No structured API |

**Sources:**
- AgentCore Web Search capabilities: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html
- AWS blog (multi-source grounding, Knowledge Graph): https://aws.amazon.com/blogs/aws/announcing-web-search-on-amazon-bedrock-agentcore-ground-your-ai-agents-in-current-accurate-web-knowledge/
- Tavily about page: https://docs.tavily.com/documentation/about

---

## Data Residency & Security

| Metric | AgentCore Web Search | Tavily | DuckDuckGo |
|--------|---------------------|--------|------------|
| Data stays in AWS | ✅ Yes — zero data egress | ❌ No — queries sent to external API | ❌ No |
| API key management | None — uses IAM role (GATEWAY_IAM_ROLE) | Requires API key stored in Secrets Manager | N/A |
| Enterprise governance | Full AWS IAM, CloudTrail, VPC support | SOC 2 compliant (per trust center) | No enterprise features |

**Sources:**
- AgentCore "Private by design": https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html
- Tavily security: https://trust.tavily.com/

---

## Projected Cost for Blog Discovery Pipeline

**Assumptions:**
- 360 teams per sport
- ~5 search queries per team per weekly discovery run
- 1,800 queries per week

| Tool | Weekly cost | Monthly cost (4 runs) |
|------|-------------|-----------------------|
| AgentCore Web Search | $12.60 | ~$50 |
| Tavily | $14.40 | ~$58 |
| DuckDuckGo | $0 | $0 (but unreliable — no official API, aggressive rate limiting) |

---

## Recommendation

**Amazon Bedrock AgentCore Web Search** is the recommended choice:

1. **No external dependency** — no API key to manage, no third-party service to monitor
2. **Data residency** — queries never leave AWS, meeting enterprise governance requirements
3. **Comparable cost** — slightly cheaper than Tavily ($7 vs $8 per 1,000 queries)
4. **Higher throughput** — 10 TPS default = 600 queries/minute (adjustable), sufficient for 360 teams
5. **LLM-optimized** — returns semantic snippets and publication dates, matching what the discovery agent needs
6. **Integrated** — works via MCP on AgentCore Gateway, same infrastructure as the rest of the pipeline

DuckDuckGo is unsuitable for production due to lack of an official API and aggressive rate limiting on programmatic access.
