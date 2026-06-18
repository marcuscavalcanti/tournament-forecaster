# World Cup Run Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 8da0c85 audit findings into durable runtime guarantees: correct event correlation keys, provable shrinkage under pathological correlated signals, honest opponent-room usage metadata, market challenge coverage, and clean auditability.

**Architecture:** Fix the root data contract first, then prove the statistical behavior, then improve observability and editorial truth. The highest-risk bug is not presentation: `correlation_group` can silently derive `match_event:*:marrocos` for unrelated matches, which makes shrinkage unreliable exactly where it is supposed to protect the model.

**Tech Stack:** Python, `uv`, pytest, JSON artifacts, `worldcup_brazil/monte_carlo.py`, `worldcup_brazil/pipeline.py`, renderer/post template tests.

---

## Execution Strategy

Use separate agents/worktrees only when files do not overlap. Merge in priority order below. Do not batch P0 with later presentation fixes.

**Parallel lanes:**

- **Lane A/P0:** `monte_carlo.py` event-correlation derivation. Must land first.
- **Lane B/P1:** artifact observability (`run_id`, removed/preflight agent metadata, opponent-room metadata wording). Can run in parallel, but merge after P0 to avoid noisy validation.
- **Lane C/P2:** market challenge from structured evidence. Independent from P0, but lower priority.
- **Lane D/P3:** post/editorial copy and model-participation rendering. Independent, lowest priority.

**Global verification after each merged lane:**

```bash
uv run --with pytest python -m pytest -q
uv run python scripts/validate_opponent_room_contract.py
uv run python scripts/validate_blind_peer_review_contract.py
```

If a validator script does not exist in a local checkout, do not invent success; run `make validate` instead.

---

## Priority 0: Calendar-Anchored Team Context Correlation

**Why this is first:** shrinkage depends on grouping correlated families by the same real-world shock. The old design inferred opponents from free text/source URLs; in run `8da0c85`, this created many `match_event:*:marrocos` groups for teams that did not play Marrocos. The fix is not a better regex. The fix is to anchor result-reactive signals to `completed_group_matches`, the authoritative completed-match calendar already present in config.

**Scope v1 decisions:**

- For result-reactive families, completed-match calendar wins over model-provided `shock_id`.
- Model `shock_id`/`correlation_group` is a hint, not authority, for result-reactive families. If it contradicts the completed-match calendar, discard the grouping hint and keep the signal.
- For non-calendar shocks, model `shock_id` can be used because the calendar cannot express those events.
- `injuries_cuts_news` and `recent_news` are hybrid. In v1, default them to `match_reactive` when there is a completed match for the team. Structural split is hook-only: record model-provided `event_scope` later, but do not let it prevent merge in v1.
- Use existing `_normalize` on config teams and signal teams.
- Forward-looking preview separation is deferred in v1. If a hybrid family has a completed match for the team, calendar anchoring wins. This intentionally over-merges some preview/news signals because over-merge dampens a signal instead of amplifying it.
- `rho=0.7` calibration and sub-threshold systemic delta detection are explicitly deferred, not dropped.

**Constants to add or centralize in `worldcup_brazil/monte_carlo.py`:**

```python
EVENT_REACTIVE_SIGNAL_FAMILIES = {
    "bets_prediction_markets",
    "ratings",
    "performance",
    "injuries_cuts_news",
    "recent_news",
}

STRUCTURAL_SIGNAL_FAMILIES = {
    "elenco_talento",
    "squad_depth",
    "tactical_cycle",
    "managerial_structure",
}
```

If local canonical names differ, map into these names through `_canonical_signal_family`; do not create parallel naming.

**Files:**

- Modify: `worldcup_brazil/monte_carlo.py`
- Test: `tests/test_monte_carlo.py`
- Optional create: `scripts/replay_team_context_shrinkage.py`
- Optional modify: `Makefile`

### Task P0.1: Write the real red tests first

- [ ] **Step 1: Add red test for Brasil multi-family post-Marrocos shock**

Add near existing team-context/shrinkage tests:

```python
def test_team_context_calendar_anchor_merges_brazil_post_morocco_signals_despite_haiti_mentions() -> None:
    config = _mc_config(iterations=2000)
    config["completed_group_matches"] = [
        {
            "group": "C",
            "team_a": "Brasil",
            "team_b": "Marrocos",
            "score_a": 1,
            "score_b": 1,
            "date": "2026-06-13",
            "source_url": "https://example.com/brazil-morocco",
        }
    ]
    config["monte_carlo"]["team_context_correlation_default_rho"] = 0.7
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {
                "category": "performance",
                "rating_delta": -10.8,
                "confidence": 1.0,
                "rationale": "Brasil empatou com Marrocos e mostrou fragilidade de criação.",
                "source_url": "https://example.com/brazil-morocco-performance",
            },
            {
                "category": "injuries_cuts_news",
                "rating_delta": -8.0,
                "confidence": 1.0,
                "rationale": "Neymar segue fora da preparação para Haiti depois da estreia contra Marrocos.",
                "source_url": "https://example.com/neymar-haiti-preview",
            },
            {
                "category": "ratings",
                "rating_delta": -13.0,
                "confidence": 1.0,
                "rationale": "Ratings reagiram ao Brasil 1-1 Marrocos.",
                "source_url": "https://example.com/brazil-rating",
            },
        ]
    }

    adjusted = run_brazil_monte_carlo(config)
    brazil = next(item for item in adjusted["team_context"]["team_adjustments"] if item["team"] == "Brasil")
    groups = {item["correlation_group"]: item for item in brazil["correlation_adjustments"]}

    assert "match_event:brasil:marrocos:2026-06-13" in groups
    shock = groups["match_event:brasil:marrocos:2026-06-13"]
    assert set(shock["member_families"]) == {"injuries_cuts_news", "performance", "ratings"}
    assert shock["residual_delta"] != 0
    assert shock["rating_delta"] > -31.8  # not blind sum of -10.8 -8.0 -13.0
```

This must fail before implementation because the old code splits by text tokens or trusts inconsistent grouping.

- [ ] **Step 2: Add red test for unrelated Marrocos mentions**

```python
def test_team_context_calendar_anchor_does_not_route_other_completed_matches_to_morocco() -> None:
    config = _mc_config(iterations=2000)
    config["completed_group_matches"] = [
        {"group": "E", "team_a": "Alemanha", "team_b": "Curaçau", "score_a": 7, "score_b": 1, "date": "2026-06-14", "source_url": "https://example.com/germany-curacao"},
        {"group": "F", "team_a": "Holanda", "team_b": "Japão", "score_a": 2, "score_b": 2, "date": "2026-06-14", "source_url": "https://example.com/netherlands-japan"},
        {"group": "F", "team_a": "Suécia", "team_b": "Tunísia", "score_a": 5, "score_b": 1, "date": "2026-06-15", "source_url": "https://example.com/sweden-tunisia"},
    ]
    config["monte_carlo"]["team_context"] = {
        "Alemanha": [{"category": "performance", "rating_delta": 8.7, "confidence": 1.0, "rationale": "Germany beat Curaçao; Brazil and Morocco are only group-context references.", "source_url": "https://example.com/germany-curacao"}],
        "Holanda": [{"category": "performance", "rating_delta": -1.4, "confidence": 1.0, "rationale": "Netherlands drew Japan; Morocco appears in a bracket sidebar.", "source_url": "https://example.com/netherlands-japan"}],
        "Suécia": [{"category": "performance", "rating_delta": 10.9, "confidence": 1.0, "rationale": "Sweden beat Tunisia; Morocco is irrelevant here.", "source_url": "https://example.com/sweden-tunisia"}],
    }

    adjusted = run_brazil_monte_carlo(config)
    groups = {
        (item["team"], group["correlation_group"])
        for item in adjusted["team_context"]["team_adjustments"]
        for group in item["correlation_adjustments"]
    }

    assert ("Alemanha", "match_event:alemanha:curacao:2026-06-14") in groups
    assert ("Holanda", "match_event:holanda:japao:2026-06-14") in groups
    assert ("Suécia", "match_event:suecia:tunisia:2026-06-15") in groups
    assert not any(group.startswith("match_event:") and ":marrocos" in group for _team, group in groups)
```

- [ ] **Step 3: Add over-merge red/protection test**

```python
def test_team_context_structural_signal_does_not_collapse_into_completed_match() -> None:
    config = _mc_config(iterations=2000)
    config["completed_group_matches"] = [
        {"group": "C", "team_a": "Brasil", "team_b": "Marrocos", "score_a": 1, "score_b": 1, "date": "2026-06-13", "source_url": "https://example.com/brazil-morocco"}
    ]
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {"category": "performance", "rating_delta": -10.0, "confidence": 1.0, "rationale": "Brasil 1-1 Marrocos.", "source_url": "https://example.com/performance"},
            {"category": "elenco_talento", "rating_delta": 6.0, "confidence": 1.0, "rationale": "Brasil segue com elenco profundo e talento estrutural.", "source_url": "https://example.com/talent"},
        ]
    }

    adjusted = run_brazil_monte_carlo(config)
    brazil = next(item for item in adjusted["team_context"]["team_adjustments"] if item["team"] == "Brasil")
    groups = {item["correlation_group"]: item for item in brazil["correlation_adjustments"]}

    assert "match_event:brasil:marrocos:2026-06-13" in groups
    assert groups["match_event:brasil:marrocos:2026-06-13"]["member_families"] == ["performance"]
    assert "elenco_talento" in groups
```

- [ ] **Step 4: Verify red**

Run each new test individually:

```bash
uv run --with pytest python -m pytest tests/test_monte_carlo.py::test_team_context_calendar_anchor_merges_brazil_post_morocco_signals_despite_haiti_mentions -q
uv run --with pytest python -m pytest tests/test_monte_carlo.py::test_team_context_calendar_anchor_does_not_route_other_completed_matches_to_morocco -q
uv run --with pytest python -m pytest tests/test_monte_carlo.py::test_team_context_structural_signal_does_not_collapse_into_completed_match -q
```

Expected before implementation: at least the first two fail for the old routing.

### Task P0.2: Implement calendar-anchored grouping

- [ ] **Step 1: Add helpers in `monte_carlo.py`**

Implement narrowly:

- `_completed_matches_by_team(config) -> dict[str, list[dict]]`
- `_signal_event_date(signal) -> str | None`
- `_latest_completed_match_for_signal(config, team, signal) -> dict | None`
- `_signal_event_scope(signal, source_family) -> "match_reactive" | "structural" | "forward_looking"`

Rules:

- Normalize teams through existing `_normalize`.
- For event-reactive families, default to `match_reactive`.
- For `injuries_cuts_news` and `recent_news`, model-provided `event_scope` is at most a future/audit hint; it does not override match-reactive v1.
- For structural families, return structural and never calendar-anchor.
- If signal has a date, choose the most recent completed match on or before that date.
- If no signal date, choose the latest completed match for that team.
- If no completed match exists, fall back safely to source family. Do not derive `match_event` from free text or model-provided labels for event-reactive families.

- [ ] **Step 2: Change correlation group resolution**

Resolution order:

1. If source family is structural: use explicit non-empty group if present, else source family.
2. If source family is event-reactive and a completed match exists: return `match_event:<team>:<opponent>:<date>`.
3. Else fallback to source family for event-reactive families. Do not use model explicit groups or free text to invent `match_event`.
4. Model explicit groups remain usable only for non-event-reactive/non-calendar signals.

Also add metadata where feasible:

- `correlation_group_source`: `completed_match`, `explicit`, `fallback_family`, `structural`
- `event_scope_hint`: value from signal, if present
- `event_scope`: runtime decision

- [ ] **Step 3: Run focused tests**

```bash
uv run --with pytest python -m pytest tests/test_monte_carlo.py::test_team_context_calendar_anchor_merges_brazil_post_morocco_signals_despite_haiti_mentions tests/test_monte_carlo.py::test_team_context_calendar_anchor_does_not_route_other_completed_matches_to_morocco tests/test_monte_carlo.py::test_team_context_structural_signal_does_not_collapse_into_completed_match -q
```

Expected: all pass.

### Task P0.3: Add runtime gate metadata for under/over-merge

- [ ] **Step 1: Add warning tests**

Add tests for warnings in `team_context["warnings"]`:

```python
def test_team_context_warns_when_reactive_families_cannot_calendar_anchor() -> None:
    config = _mc_config(iterations=2000)
    config["completed_group_matches"] = []
    config["monte_carlo"]["team_context"] = {
        "Brasil": [
            {"category": "performance", "rating_delta": -6.0, "confidence": 1.0, "source_url": "https://example.com/perf"},
            {"category": "ratings", "rating_delta": -6.0, "confidence": 1.0, "source_url": "https://example.com/rating"},
        ]
    }

    adjusted = run_brazil_monte_carlo(config)
    assert any(warning.get("reason") == "team_context_reactive_families_without_calendar_anchor" for warning in adjusted["team_context"]["warnings"])
```

For over-merge, assert structural family never appears in `match_event`; if implementation includes warning, assert `team_context_structural_family_in_match_event` never appears in healthy path.

- [ ] **Step 2: Implement warning**

After group assembly, if `rho > 0` and a team has two or more event-reactive families but no completed-match calendar anchor, add warning:

```python
{
    "team": team,
    "reason": "team_context_reactive_families_without_calendar_anchor",
    "source_families": [...],
}
```

Do not fail the run in v1; make it visible.

- [ ] **Step 3: Run broader team-context tests**

```bash
uv run --with pytest python -m pytest tests/test_monte_carlo.py -q
```

### Task P0.4: Add shrinkage replay command

- [ ] **Step 1: Create optional diagnostic script**

Create `scripts/replay_team_context_shrinkage.py` with no LLM/web calls. It should build the canonical Brasil post-Marrocos config, print:

- blind-sum delta,
- calendar-anchored rho=0.7 delta,
- group details including `residual_delta`.

- [ ] **Step 2: Add Makefile target**

```make
shrinkage-check:
	uv run python scripts/replay_team_context_shrinkage.py
```

- [ ] **Step 3: Commit**

```bash
git add worldcup_brazil/monte_carlo.py tests/test_monte_carlo.py scripts/replay_team_context_shrinkage.py Makefile
git commit -m "fix: anchor team context correlation to completed matches"
```

---

## Priority 2: Fix Run Auditability Metadata

**Why third:** this does not change the number, but it prevents recurring mis-attribution in audits and makes degraded runs visible.

**Files:**

- Modify: `worldcup_brazil/pipeline.py`
- Modify: `worldcup_brazil/models.py` if wrapper serialization lives there
- Test: `tests/test_market_value_momentum.py` or `tests/test_source_planning_harness.py`
- Test: `tests/test_post_template.py`

### Task P2.1: Duplicate run_id at wrapper/root artifact level

- [ ] **Step 1: Add/adjust test**

Use an existing artifact build test and assert both:

```python
assert artifacts.bundle.run_id == "test-run-market-value"
assert artifacts.bundle.metadata["run_id"] == "test-run-market-value"
```

If the JSON writer wraps as `{"bundle": ..., "evidence": ...}`, add a writer-level test asserting:

```python
assert payload["run_id"] == "test-run-market-value"
assert payload["bundle"]["metadata"]["run_id"] == "test-run-market-value"
```

- [ ] **Step 2: Implement**

When writing `outputs/linkedin_brazil_*.json`, include `run_id` at the top-level wrapper as well as inside `bundle`.

- [ ] **Step 3: Commit**

```bash
git add worldcup_brazil/pipeline.py tests/test_market_value_momentum.py
git commit -m "chore: expose run id at artifact root"
```

### Task P2.2: Persist preflight-excluded agents into metadata

- [ ] **Step 1: Add test**

Simulate an Opus preflight exclusion and assert metadata contains:

```python
assert "Opus 4.8" in bundle.metadata["preflight_excluded_agent_slots"]
assert "session limit" in bundle.metadata["preflight_excluded_agent_reasons"]["Opus 4.8"].lower()
```

- [ ] **Step 2: Implement**

In `build_report_bundle`, carry hard preflight exclusions separately from source-planning removals:

- `preflight_excluded_agent_slots`
- `preflight_excluded_agent_reasons`
- `active_agent_slots`
- `configured_agent_slots`

Do not conflate these with `removed_agent_slots`, which currently means source-planning removal.

- [ ] **Step 3: Commit**

```bash
git add worldcup_brazil/pipeline.py tests/test_source_planning_harness.py
git commit -m "chore: persist preflight agent exclusions"
```

---

## Priority 3: Clarify Opponent Room Metadata

**Why fourth:** the run showed `degraded_shadow_only=true` even when `exit_status=consensus`. That is technically a degraded-route knob, but it is easy to misread as “whole room shadow only.”

**Files:**

- Modify: `worldcup_brazil/pipeline.py`
- Test: `tests/test_source_planning_harness.py`
- Optional: `worldcup_brazil/debate_report.py`

### Task P3.1: Split normal consensus fields from degraded-route fields

- [ ] **Step 1: Add test**

In the side-room consensus test, assert:

```python
briefing = artifacts.bundle.metadata["_parallel_opponent_briefing"]
assert briefing["usable_for_main_room"] is True
assert briefing["exit_status"] == "consensus"
assert briefing["applied_to_main_room"] is True
assert briefing["degraded_route"]["shadow_only"] is True
assert briefing["degraded_route"]["active"] is False
```

- [ ] **Step 2: Implement**

Change `_parallel_opponent_briefing_for_prompt` and metadata payload to expose:

```python
"applied_to_main_room": bool(result.get("usable_for_main_room", False)),
"degraded_route": {
    "active": bool(result.get("degraded", False)),
    "shadow_only": bool(result.get("degraded_shadow_only", True)),
    "would_be_usable": bool(result.get("degraded_would_be_usable", False)),
}
```

Keep old fields for backward compatibility for one release if tests depend on them, but prefer the new fields in reports.

- [ ] **Step 3: Commit**

```bash
git add worldcup_brazil/pipeline.py tests/test_source_planning_harness.py
git commit -m "chore: clarify opponent room application metadata"
```

---

## Priority 4: Make Market Challenge Less Dependent on Debate Prose

**Why fifth:** `market_title_challenge` was `no_market_signal`. That is not a crash, but it means the published title lacks market cross-check even when source plans include odds/market URLs.

**Files:**

- Modify: `worldcup_brazil/pipeline.py`
- Test: `tests/test_pipeline_match_application.py`

### Task P4.1: Extract market evidence from structured source/evidence fields

- [ ] **Step 1: Add red test**

Create a test where debate prose contains no explicit market percentage, but source labels include odds URLs/snippets:

```python
def test_market_title_challenge_can_use_structured_market_evidence_when_debate_has_no_percentage() -> None:
    stage = {"titulo": 5.9}
    transcript = [{"round": 1, "responses": [{"answer": "Mantenho o baseline sem nova porcentagem de mercado."}]}]
    config = {
        "market_title_challenge": {
            "enabled": True,
            "absolute_gap_pct": 3.0,
            "relative_gap_pct": 0.40,
        },
        "_market_evidence": [
            {"source_url": "https://example.com/outright-odds", "text": "Brazil World Cup outright odds 10/1"},
        ],
    }

    challenge = _market_title_challenge(stage, transcript, config=config)

    assert challenge["status"] != "no_market_signal"
```

- [ ] **Step 2: Implement minimally**

Feed structured evidence into `_market_title_challenge` via config/metadata. Parse only high-confidence outright/title odds. Do not parse match odds or group match odds.

- [ ] **Step 3: Protect against false positives**

Add a control test:

```python
assert _market_title_challenge({"titulo": 5.9}, transcript_with_brazil_haiti_match_odds, config=config)["status"] == "no_market_signal"
```

- [ ] **Step 4: Commit**

```bash
git add worldcup_brazil/pipeline.py tests/test_pipeline_match_application.py
git commit -m "feat: use structured market evidence for title challenge"
```

---

## Priority 5: Guard Consensus Quality on Stance Flips

**Why sixth:** the 8da0c85 run had a visible flip: DeepSeek opposed the Japan 65% adjustment in round 5, then as protagonist treated 65% as audited consensus in round 6 without new evidence.

**Files:**

- Modify: `worldcup_brazil/pipeline.py`
- Test: `tests/test_meeting.py` or `tests/test_meeting_flow_controls.py`

### Task P5.1: Add stance-change rationale warning

- [ ] **Step 1: Add test**

Construct a transcript where the same agent:

1. Disagrees with a match probability in round N.
2. As protagonist in round N+1 declares that same probability consensus.
3. Provides no new source URL/query.

Assert warning:

```python
assert any("mudança de posição sem nova fonte" in warning.lower() for warning in bundle.warnings)
```

- [ ] **Step 2: Implement warning-only**

Do not block the run. Add an audit warning or watchdog event. This is a debate-quality issue, not a coherence failure.

- [ ] **Step 3: Commit**

```bash
git add worldcup_brazil/pipeline.py tests/test_meeting_flow_controls.py
git commit -m "chore: warn on unexplained model stance flips"
```

---

## Priority 6: Editorial/Rendering Cleanups

**Why last:** these improve post clarity but do not change the simulation engine.

**Files:**

- Modify: `worldcup_brazil/post_template.py`
- Modify: `worldcup_brazil/renderer.py`
- Test: `tests/test_post_template.py`, `tests/test_renderer.py`

### Task P6.1: Make "what changed" concrete

- [ ] **Step 1: Add post-template test**

Assert the post can cite specific run-to-run drivers when metadata contains them:

```python
assert "team_context" in post or "sala lateral" in post or "correlation_group" in post
```

- [ ] **Step 2: Implement dynamic bullets**

Prefer concrete changes:

- title changed by X pp
- side room usable/not usable
- team_context delta changed
- market challenge triggered/not triggered

- [ ] **Step 3: Commit**

```bash
git add worldcup_brazil/post_template.py tests/test_post_template.py
git commit -m "chore: render concrete run-over-run changes"
```

---

## Final Verification

After all lanes merge:

```bash
uv run --with pytest python -m pytest -q
make validate
make debate
make profile -- --run-id 8da0c85fb88a48ecab11fac689f39b79
```

Then run one no-LLM diagnostic:

```bash
make shrinkage-check
```

Do not require `make force` to merge these fixes. `make force` is the live external-cost proof after merge.

---

## Commit Order

1. `fix: prevent false opponent defaults in team context grouping`
2. `test: add team context shrinkage replay harness`
3. `chore: expose run id at artifact root`
4. `chore: persist preflight agent exclusions`
5. `chore: clarify opponent room application metadata`
6. `feat: use structured market evidence for title challenge`
7. `chore: warn on unexplained model stance flips`
8. `chore: render concrete run-over-run changes`

---

## Self-Review

**Spec coverage:** Covers the audit findings from run `8da0c85`: false `:marrocos` grouping, shrinkage not exercised, wrapper run_id, preflight Opus invisibility, opponent-room metadata ambiguity, market `no_market_signal`, Japan stance flip, and generic post deltas.

**Critical path:** P0 and P1 must happen before claiming shrinkage reliability. P2-P6 can improve operations independently.

**Risk control:** P0 is the only engine-changing fix. P2/P3/P5/P6 are mostly metadata/rendering. P4 must stay conservative to avoid parsing match odds as title odds.
