"""Event coverage comparison between baseline and challenger model runs."""


def event_signature(event: dict) -> str:
    """Create a normalized matching key for an event."""
    return "|".join(
        [
            event.get("team", "").lower().strip(),
            event.get("event_type", "").upper().strip(),
            (event.get("player_name") or "").lower().strip(),
            event.get("source_url", "").strip(),
        ]
    )


def compute_coverage(
    baseline_events: list[dict], challenger_events: list[dict]
) -> dict:
    """Compare events found by baseline vs challenger.

    Returns coverage metrics showing how well the challenger captures baseline events.
    """
    baseline_sigs = {event_signature(e) for e in baseline_events}
    challenger_sigs = {event_signature(e) for e in challenger_events}

    overlap = baseline_sigs & challenger_sigs

    return {
        "baseline_count": len(baseline_sigs),
        "challenger_count": len(challenger_sigs),
        "overlap_count": len(overlap),
        "baseline_only_count": len(baseline_sigs) - len(overlap),
        "challenger_only_count": len(challenger_sigs) - len(overlap),
        "recall_vs_baseline": (
            len(overlap) / len(baseline_sigs) if baseline_sigs else 1.0
        ),
        "precision_vs_baseline": (
            len(overlap) / len(challenger_sigs) if challenger_sigs else 1.0
        ),
    }


def compare_runs(baseline_run: dict, challenger_run: dict) -> dict:
    """Compare two full evaluation runs (output JSONs from runner).

    Returns per-team and aggregate coverage metrics.
    """
    baseline_by_team = {
        r["team"]: r["events"] for r in baseline_run["team_results"]
    }
    challenger_by_team = {
        r["team"]: r["events"] for r in challenger_run["team_results"]
    }

    per_team = {}
    all_baseline_events = []
    all_challenger_events = []

    for team in baseline_by_team:
        b_events = baseline_by_team.get(team, [])
        c_events = challenger_by_team.get(team, [])
        all_baseline_events.extend(b_events)
        all_challenger_events.extend(c_events)
        per_team[team] = compute_coverage(b_events, c_events)

    aggregate = compute_coverage(all_baseline_events, all_challenger_events)

    return {
        "baseline_model": baseline_run["model_config"]["label"],
        "challenger_model": challenger_run["model_config"]["label"],
        "per_team": per_team,
        "aggregate": aggregate,
    }
