# Blog Discovery & Extraction Pipeline — Sprint Plan

## Design Principles

- **Sports-agnostic**: The user provides a sport + list of entities (teams/players). The pipeline scales to any sport with zero code changes.
- **No seed URLs required**: The discovery agent autonomously finds blogs/forums via web search, validates them, and populates the registry.
- **Domain config driven**: Each sport is defined by a config specifying entities, event categories, and search query templates.
- **Local-first development**: All agent logic, tools, and prompts are developed and tested locally before any infra deployment. Infra (DynamoDB, Lambda, Step Functions, EventBridge) comes later.

---

## Domain Config Example

```yaml
# domains/ncaa_mbb.yaml
domain_id: ncaa_mbb
display_name: "NCAA Men's Basketball"
# No hardcoded entity list — the agent discovers all teams automatically.
entity_discovery:
  method: web_search
  queries:
    - "complete list of NCAA Division 1 men's basketball teams {year}"
    - "all D1 college basketball programs by conference"
  authoritative_sources:
    - "espn.com"
    - "ncaa.com"
    - "sports-reference.com"
event_categories:
  - injury
  - venue_update
  - schedule_update
blog_discovery_queries:
  - "{entity} college basketball fan forum"
  - "{entity} basketball blog rumors injuries"
  - "{entity} hoops community discussion board"
extraction_prompt_template: |
  You are analyzing fan blog content for {entity} ({display_name}).
  Extract events in these categories: {categories}.
  Focus on: player injuries, game schedule changes, venue updates.
  Return structured JSON matching the output schema.
schedule_lookahead_days: 3
```

Adding a new sport = adding a new config. The agent discovers all teams/entities and their blogs autonomously. No manual entity lists or seed URLs.

---

## Tickets: Blog Discovery Pipeline

### Local (no infra) — 10h

Only python + Bedrock API access.

- Design config model to enable configuration for different sports
- Build tools: `web_search`, `access_check`, `recency_check`, `registry_write` (local JSON). Local JSON backend swaps to DynamoDB in a separate task.
- Write discovery agent prompt: each run fetches all team names for the sport, then per team discovers blog URLs and checks accessibility + recency. On re-runs, adds new URLs and updates flags for existing ones.
- Add Pydantic validation for discovery outputs
- Store output as JSON. Schema per team: sport name, team, conference, list of blog URLs each with access check flag ("accessible"/error code) and recency check flag ("active"/"outdated").

---

## Tickets: Blog Extraction Agent

### E-1: Extraction Agent — Local (5h, no infra)

**Goal**: Given a team name and its pre-discovered blog URLs (from the discovery pipeline JSON output), fetch the blog content and extract structured events.

**Input**: Team name + list of blog URLs from discovery output (filtered to accessible + active only).

**Output**: Structured JSON matching the architecture schema:
```json
{
  "events": [
    {
      "sport": "string (sport name)",
      "team": "string",
      "web_link": "string (source URL)",
      "category": "injury | venue_update | schedule_update",
      "player_name": "string",
      "summary": "string (LLM summary)",
      "raw_excerpt": "string (verbatim text from source)",
      "event_timestamp": "ISO-8601",
      "confidence": 0.0-1.0
    }
  ]
}
```

**Tasks**:
- Build `http_fetch` tool — fetches full HTML, strips boilerplate/nav, returns clean text (0.5h)
- Build `rss_fetch` tool — parses RSS/Atom feeds, returns latest entries with title, link, date, summary (0.5h)
- Define Pydantic output schema (`BlogEvent`, `BlogExtractionResult`) matching the structured output above (0.5h)
- Write extraction agent prompt — reads team + URLs from input, calls `http_fetch`/`rss_fetch` on each, extracts events matching categories, returns validated structured JSON (1.5h)
- Local test: run extraction agent for 3 teams using real URLs from discovery output. Validate schema compliance, iterate on prompt (2h)

**Acceptance criteria**:
- Agent reads pre-discovered URLs from local JSON (output of discovery pipeline)
- Agent fetches and parses real blog content
- Output passes Pydantic validation
- Works for any sport (category enum comes from domain config)
- Returns empty events list (not an error) when no relevant information found

---

### E-2: Extraction Agent — Lambda + DynamoDB (3h, requires infra)

**Goal**: Deploy the extraction agent as a Lambda that processes a single team and writes structured events to DynamoDB.

**Input**: `{domain_id, team, source_urls[]}`

**Output**: Extracted events written to DynamoDB Events table.

**Tasks**:
- Create DynamoDB Events table — PK: `sport#team`, SK: `event_timestamp#hash`, GSI on category (0.5h)
- Build `events_write` tool — persists events to DynamoDB, deduplicates by web_link + team (0.5h)
- Package extraction agent as Lambda — Dockerfile, Bedrock + DynamoDB + Blog Registry read permissions (1h)
- Integration test: invoke Lambda manually for 3 teams, verify events in DynamoDB (1h)

**Acceptance criteria**:
- Lambda invocable with `{domain_id, team, source_urls[]}`
- Output passes Pydantic validation before writing to DynamoDB
- Events deduplicated — re-runs don't create duplicate entries
- Reads blog URLs from Blog Registry if `source_urls` not provided

---

### E-3: Step Functions Orchestration + Scheduling (3h, requires infra)

**Goal**: Orchestrate the extraction Lambda per team on an hourly schedule using Step Functions.

**Input**: EventBridge hourly cron triggers Step Functions with `{domain_id}`.

**Output**: All active teams for the sport get their blogs processed in parallel.

**Tasks**:
- Create DynamoDB Schedule table — teams active in next N days per sport (0.5h)
- Create Step Functions state machine — reads active teams from Schedule table, fans out extraction Lambda per team via Map state with MaxConcurrency=N. Retry with exponential backoff, per-team error isolation (1.5h)
- Deploy EventBridge hourly cron — triggers Step Functions with domain_id (0.5h)
- End-to-end test: trigger for 1 conference (~15 teams), validate events in DynamoDB, verify failure isolation (0.5h)

**Acceptance criteria**:
- Step Functions handles 360+ teams without timeout (Map state parallelism)
- One team's failure doesn't block others
- MaxConcurrency tunable to stay within Bedrock TPM/RPS limits
- CloudWatch logs show per-team execution status

---

## Architecture Flow Summary

```
User provides: Sport Config (e.g. "NCAA Men's Basketball")
         │
         ▼
┌─────────────────────────────────────┐
│  Domain Config (YAML)               │
│  - entity_discovery queries         │
│  - blog_discovery_queries           │
│  - event_categories                 │
│  - extraction_prompt_template       │
└────────────┬────────────────────────┘
             │
     ┌───────┼───────────────┐
     ▼       ▼               ▼
┌──────────┐ ┌──────────┐ ┌──────────────────┐
│ Entity   │ │ Blog     │ │ Extraction       │
│ Discovery│ │ Discovery│ │ (hourly)         │
│ (monthly)│ │ (weekly) │ │                  │
│          │ │          │ │ For each active  │
│ Find all │ │ Per team:│ │ entity:          │
│ teams in │ │ 1. Search│ │ 1. Get URLs from │
│ the sport│ │ 2. Access│ │    registry      │
│ from     │ │    check │ │ 2. http_fetch    │
│ authority│ │ 3. Recncy│ │ 3. rss_fetch     │
│ sources  │ │    check │ │ 4. Extract events│
│          │ │ 4. Write │ │ 5. Write to DB   │
└──────────┘ └──────────┘ └──────────────────┘
```

---

### D-2: Blog Discovery Pipeline — Lambda + Infra (5h, requires infra)

**Goal**: Deploy the blog discovery agent as a Lambda triggered weekly. Each run: discovers all teams for the sport, then discovers/updates blog URLs per team.

**Input**: EventBridge weekly cron triggers Lambda with `{domain_id}`.

**Output**: DynamoDB Blog Registry populated with all teams and their blog URLs, each with accessibility and recency flags.

**DynamoDB Blog Registry schema**:
- PK: `{sport}#{team}` (e.g. `ncaa_mbb#Duke`)
- Attributes per item: team, sport, conference, blogs (list of objects)
- Each blog object: `{ url, platform, accessible (bool or error code), recency_status ("active"/"outdated"), last_checked, last_post_date }`

**Tasks**:
- Create DynamoDB Blog Registry table with PK `sport#team`, blog list per item (0.5h)
- Swap `registry_write` tool from local JSON to DynamoDB — same interface (0.5h)
- Package discovery agent as Lambda — Dockerfile, Bedrock + DynamoDB permissions (1h)
- Deploy EventBridge weekly cron — triggers discovery Lambda with domain_id (0.5h)
- Integration test: run discovery Lambda for 1 conference (~15 teams), verify DynamoDB populated correctly (1.5h)
- Buffer for debugging permissions / timeout issues (1h)

**Acceptance criteria**:
- Single weekly run does both: fetch all teams for the sport, then discover/update blogs per team
- New teams get added, existing teams get their blog list updated (new URLs added, stale ones flagged)
- Single table stores everything: sport, team, conference, and list of blogs with flags
- Lambda invocable independently with `{domain_id}` or `{domain_id, team}` for single-team runs
- CloudWatch logs show per-team discovery status

---

## Summary

| Ticket | Scope | Hours |
|--------|-------|-------|
| D-1: Blog Discovery Pipeline | Local, no infra | 10h |
| D-2: Blog Discovery — Lambda + Infra | Requires infra | 5h |
| E-1: Extraction Agent | Local, no infra | 5h |
| E-2: Extraction — Lambda + DynamoDB | Requires infra | 3h |
| E-3: Step Functions Orchestration + Scheduling | Requires infra | 3h |
| **Total** | | **26h** |

## Sprint Dependency Summary

| Sprint | Focus | Infra Required? |
|--------|-------|-----------------|
| 1 | D-1 through D-9, E-1 through E-6 (all local work) | ❌ No |
| 2 | D-10 through D-15, E-7 through E-15 (all infra work) | ✅ Yes |

---

## What's NOT in scope (future)

- UI for managing sports/entities
- Real-time alerting on high-confidence events
- Feedback loop (human marks events correct/incorrect → improves prompts)
- Multi-language blog support
