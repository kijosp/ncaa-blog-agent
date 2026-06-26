# Blog Discovery Pipeline

## How It Works

```mermaid
flowchart TD
    %% Styling
    classDef input fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef agent fill:#fef3c7,stroke:#d97706,color:#92400e
    classDef check fill:#f0fdf4,stroke:#16a34a,color:#166534
    classDef output fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef filter fill:#fee2e2,stroke:#dc2626,color:#991b1b

    %% Flow
    CONFIG[("⚙️ Configuration<br/><i>Sport, teams list,<br/>search parameters</i>")]:::input

    DISCOVER["🔍 STEP 1: Find Teams<br/><br/>AI searches the web to find<br/>all 365 NCAA Men's Basketball<br/>teams organized by conference"]:::agent

    SEARCH["🌐 STEP 2: Find Fan Blogs<br/><br/>For each team, AI searches for:<br/>• Fan forums & message boards<br/>• Independent blogs<br/>• Team community sites<br/>• RSS feeds"]:::agent

    ACCESS{"🚦 STEP 3: Check Access<br/><br/>Can we reach the site?<br/>Is it online and working?"}:::check

    RECENCY{"📅 STEP 4: Check Freshness<br/><br/>Was it updated recently?<br/>Is it still active in 2026?"}:::check

    VERIFY["✅ STEP 5: Verify Relevance<br/><br/>Is this ACTUALLY about<br/>Men's College Basketball?<br/><i>(Not football, NBA, women's, etc.)</i>"]:::agent

    REJECT["❌ Rejected<br/><br/>• Wrong sport<br/>• Dead links (404)<br/>• Multi-sport pages"]:::filter

    REGISTRY[("📊 STEP 6: Save Results<br/><br/>Team → Blog URL registry<br/>with status & freshness<br/><br/>JSON + Excel + Dashboard")]:::output

    %% Connections
    CONFIG --> DISCOVER
    DISCOVER --> SEARCH
    SEARCH --> ACCESS
    ACCESS -->|"✅ Accessible"| RECENCY
    ACCESS -->|"❌ 404 Not Found"| REJECT
    ACCESS -->|"⚠️ 403 Blocked"| VERIFY
    RECENCY --> VERIFY
    VERIFY -->|"✅ PASS"| REGISTRY
    VERIFY -->|"❌ FAIL"| REJECT

    %% Weekly re-run note
    REGISTRY -.->|"🔄 Weekly Re-run:<br/>Only refresh freshness<br/>for known blogs"| RECENCY
```

## Pipeline at a Glance

| Step | What Happens | Why |
|------|-------------|-----|
| 🔍 Find Teams | Discovers all 365 D1 teams | Complete coverage |
| 🌐 Find Blogs | Searches web for fan sites per team | Source discovery |
| 🚦 Check Access | Verifies sites are reachable | No dead links |
| 📅 Check Freshness | Confirms sites are actively posting | Only live sources |
| ✅ Verify Relevance | Confirms it's Men's Basketball | No noise from other sports |
| 📊 Save Results | Builds searchable registry | Ready for traders |

## Key Outputs

- **Registry JSON** — Machine-readable list of all verified blog URLs per team
- **Excel Report** — Sortable spreadsheet with team, blog, status, last post date
- **Dashboard** — Visual web app to browse results by team with color-coded status

## Weekly Re-runs

On subsequent runs, the pipeline is **smart about what it re-checks**:

- 🆕 **New blogs** → Full pipeline (search → access → freshness → verify)
- 📋 **Known blogs** → Only refresh freshness (saves time & cost)
