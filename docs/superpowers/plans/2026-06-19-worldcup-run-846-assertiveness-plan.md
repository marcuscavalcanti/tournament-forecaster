# World Cup Run 846 Assertiveness Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the next daily World Cup run by making the final probabilities more externally anchored, more stress-tested, and less vulnerable to Monte Carlo echo-consensus.

**Architecture:** Keep Monte Carlo as the numerical spine, but force three independent checks before publication: structured market evidence, team-context sensitivity, and adversarial debate pressure. The final number remains governed by the existing 60/40 Monte Carlo/model blend, but the pipeline must expose when the model council merely ratifies the simulation versus when it actually moves or challenges it.

**Tech Stack:** Python 3 via `uv`, existing `worldcup_brazil` package, JSON bundle outputs, watchdog JSONL, `pytest`, existing `make validate`.

---

## Run 846 Evidence Summary

Use this as the baseline fixture when implementing.

- Run: `846af95960c448e1a2c9c6aec4bca3af`
- Artifact: `outputs/linkedin_brazil_2026-06-18.json`
- Final title: `5.1%`
- Monte Carlo title: `5.1%`
- Agent consensus title: `5.1%`
- Debate dispersion: `0.0 p.p.`
- Formal blend: `60% Monte Carlo / 40% model consensus`
- Active room: `Opus 4.8`, `GPT 5.5`, `DeepSeek V4 Pro`, `Gemini Pro`
- Removed pre-room: `Perplexity Pro`
- Team context Brazil delta: `-17.4`, anchored to `match_event:brasil:marrocos:2026-06-13`
- Market challenge status: `within_threshold`, but low bound included `5.1%`, which is suspicious because it equals the model/funil number.
- Opponent room: usable, consensus in 1 round, title `5.1%`.

The highest-value issue is not a runtime failure. It is that the run became numerically stable by ratifying the Monte Carlo. The next run needs better independent pressure on that 5.1%.

---

### Task 1: Structured Market Evidence, Not Debate-Prose Market Extraction

**Files:**
- Modify: `worldcup_brazil/pipeline.py` around `_market_title_challenge_config`, `_market_title_values_from_text`, `_market_title_challenge`
- Modify: `config/worldcup_brazil.example.json`
- Test: `tests/test_pipeline.py` or existing market-challenge test file

**Why:** In run 846, `market_title_challenge.market_low_pct` was `5.1`, equal to the model title. That means the market band can still be contaminated by the model's own number. The next run should compare the model against market evidence, not against the debate repeating the model.

- [ ] **Step 1: Write the failing test for model-number leakage**

Add a test that reproduces the run 846 pattern: debate text contains both the model title and true market odds.

```python
def test_market_title_challenge_rejects_model_title_as_market_low():
    stage_probabilities = {"titulo": 5.1}
    transcript = [
        {
            "round": 1,
            "protagonist": "Opus 4.8",
            "question": (
                "O funil do Modelo Principal está em 5.1%, mas o mercado fresco fala em "
                "Brasil 9/1 a 10/1 nas casas, com leitura bruta perto de 9%."
            ),
            "responses": [],
        }
    ]
    config = {
        "market_title_challenge": {
            "enabled": True,
            "min_market_pct": 1.0,
            "max_market_pct": 25.0,
            "robust_low_percentile": 0.2,
            "absolute_gap_pct": 3.0,
            "relative_gap_pct": 0.4,
        }
    }

    challenge = _market_title_challenge(stage_probabilities, transcript, config=config)

    assert challenge["market_low_pct"] > 5.1
    assert all("5.1" not in item["snippet"] or item["source"] != "market_candidate" for item in challenge["evidence"])
```

- [ ] **Step 2: Run the test and verify it fails on current code**

Run:

```bash
uv run --with pytest python -m pytest tests/test_pipeline.py::test_market_title_challenge_rejects_model_title_as_market_low -q
```

Expected before implementation: FAIL because `market_low_pct` remains `5.1`.

- [ ] **Step 3: Implement model-number exclusion**

In `worldcup_brazil/pipeline.py`, when building market candidates, reject any candidate that is equal to the current model title within `0.15 p.p.` unless the same sentence contains a strong market marker and an odds token independent from the model title.

Add helper:

```python
def _market_candidate_matches_model_title(value: float, model_title_pct: float) -> bool:
    return abs(round(float(value), 1) - round(float(model_title_pct), 1)) <= 0.15
```

Use it inside `_market_title_challenge` after candidate extraction:

```python
model_title_pct = round(float(stage_probabilities.get("titulo", 0.0) or 0.0), 1)
filtered_candidates = [
    candidate
    for candidate in candidates
    if not _market_candidate_matches_model_title(candidate, model_title_pct)
]
if filtered_candidates:
    candidates = filtered_candidates
```

- [ ] **Step 4: Add source-plan evidence input**

Extend `_market_title_challenge` to accept optional `source_texts: list[tuple[str, str]]` and pass source planning snippets or URLs where odds were reported. Do not fetch web here. Use only texts already captured by agents.

Signature:

```python
def _market_title_challenge(
    stage_probabilities: dict[str, float],
    meeting_transcript: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    source_texts: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
```

Call site near bundle creation should pass source planning summaries from active planning opinions.

- [ ] **Step 5: Run market tests**

Run:

```bash
uv run --with pytest python -m pytest tests/test_pipeline.py -q -k market_title
```

Expected: all market-title tests pass.

- [ ] **Step 6: Commit**

```bash
git add worldcup_brazil/pipeline.py config/worldcup_brazil.example.json tests/test_pipeline.py
git commit -m "fix: anchor market title challenge outside model title"
```

---

### Task 2: Team Context Sensitivity Harness in the Bundle

**Files:**
- Modify: `worldcup_brazil/monte_carlo.py`
- Modify: `worldcup_brazil/pipeline.py`
- Modify: `worldcup_brazil/renderer.py`
- Test: `tests/test_monte_carlo.py`, `tests/test_pipeline.py`

**Why:** Run 846 uses Brazil `rating_delta=-17.4` after shrinkage. That is much better than the old `-43.1`, but the value still depends on `rho=0.7`. The next run needs to say how much the title moves if the context penalty is relaxed or intensified.

- [ ] **Step 1: Write a failing unit test for sensitivity metadata**

Add:

```python
def test_team_context_sensitivity_reports_title_elasticity():
    result = {
        "stage_probabilities": {"titulo": 5.1},
        "team_context": {
            "team_adjustments": [
                {
                    "team": "Brasil",
                    "rating_delta": -17.4,
                    "correlation_adjustments": [
                        {
                            "correlation_group": "match_event:brasil:marrocos:2026-06-13",
                            "rho": 0.7,
                            "rating_delta": -17.4,
                        }
                    ],
                }
            ]
        },
    }

    summary = _team_context_sensitivity_summary(result)

    assert summary["enabled"] is True
    assert summary["brazil_rating_delta"] == -17.4
    assert "requires_recalc" in summary
```

- [ ] **Step 2: Implement lightweight sensitivity summary**

Do not run three full Monte Carlos inside every daily run yet. First version should emit the parameters needed for replay:

```python
def _team_context_sensitivity_summary(monte_carlo_result: dict[str, Any]) -> dict[str, Any]:
    team_context = monte_carlo_result.get("team_context") if isinstance(monte_carlo_result, dict) else {}
    adjustments = team_context.get("team_adjustments") if isinstance(team_context, dict) else []
    brazil = next((item for item in adjustments if str(item.get("team", "")).lower() == "brasil"), None)
    if not brazil:
        return {"enabled": False, "reason": "no_brazil_team_context"}
    return {
        "enabled": True,
        "brazil_rating_delta": round(float(brazil.get("rating_delta", 0.0)), 1),
        "requires_recalc": True,
        "recommended_scenarios": ["current", "rho_1_price_once", "rho_0_full_sum", "no_brazil_context"],
    }
```

- [ ] **Step 3: Store it in metadata**

In `build_report_bundle`, add under `metadata`:

```python
"team_context_sensitivity": _team_context_sensitivity_summary(monte_carlo_result),
```

- [ ] **Step 4: Render it in the audit, not the short post**

In `renderer.py`, under Monte Carlo summary:

```python
sensitivity = bundle.metadata.get("team_context_sensitivity") or {}
if sensitivity.get("enabled"):
    lines.append(
        "- Sensibilidade pendente: Brasil rating_delta "
        f"{sensitivity.get('brazil_rating_delta')} p.p. de rating; "
        "cenários recomendados: current, rho_1_price_once, rho_0_full_sum, no_brazil_context."
    )
```

- [ ] **Step 5: Run tests**

```bash
uv run --with pytest python -m pytest tests/test_monte_carlo.py tests/test_pipeline.py -q -k "team_context or sensitivity"
```

- [ ] **Step 6: Commit**

```bash
git add worldcup_brazil/monte_carlo.py worldcup_brazil/pipeline.py worldcup_brazil/renderer.py tests/test_monte_carlo.py tests/test_pipeline.py
git commit -m "feat: expose team context sensitivity hooks"
```

---

### Task 3: Anti-Echo Challenger Before Consensus Exit

**Files:**
- Modify: `worldcup_brazil/pipeline.py`
- Modify: `config/worldcup_brazil.example.json`
- Test: `tests/test_meeting.py`

**Why:** In run 846, every valid response converged to `5.1%` from round 1 onward. That is stable, but it can be too passive. If the council accepts the MC exactly, require one challenger turn that tests the number against market, bracket, and team-context sensitivity before exiting.

- [ ] **Step 1: Add config knobs**

In example config:

```json
"meeting_require_challenger_when_exact_mc_consensus": true,
"meeting_exact_mc_consensus_rounds_before_challenge": 2
```

- [ ] **Step 2: Write failing test**

Add to `tests/test_meeting.py`:

```python
def test_exact_monte_carlo_consensus_requires_challenger_before_exit():
    transcript = [
        {"round": 1, "consensus_title_pct": 5.1, "consensus_spread_pct": 0.0},
        {"round": 2, "consensus_title_pct": 5.1, "consensus_spread_pct": 0.0},
    ]
    config = {
        "_monte_carlo_result": {"stage_probabilities": {"titulo": 5.1}},
        "meeting_require_challenger_when_exact_mc_consensus": True,
        "meeting_exact_mc_consensus_rounds_before_challenge": 2,
    }

    assert _should_force_exact_mc_challenger(transcript, config) is True
```

- [ ] **Step 3: Implement helper**

```python
def _should_force_exact_mc_challenger(transcript: list[dict[str, Any]], config: dict[str, Any]) -> bool:
    if not bool(config.get("meeting_require_challenger_when_exact_mc_consensus", True)):
        return False
    needed = int(config.get("meeting_exact_mc_consensus_rounds_before_challenge", 2))
    if len(transcript) < needed:
        return False
    mc = config.get("_monte_carlo_result") if isinstance(config.get("_monte_carlo_result"), dict) else {}
    mc_title = round(float((mc.get("stage_probabilities") or {}).get("titulo", -1.0)), 1)
    recent = transcript[-needed:]
    return all(
        round(float(turn.get("consensus_title_pct", -999.0)), 1) == mc_title
        and float(turn.get("consensus_spread_pct", 999.0)) <= 0.2
        for turn in recent
    )
```

- [ ] **Step 4: Wire into meeting loop**

Before allowing early exit by consensus, if helper is true and no challenger has already run, force the next protagonist question:

```python
"A sala está aceitando exatamente o Monte Carlo. Antes de sair, faça papel de challenger: "
"qual premissa moveria o título pelo menos 1 p.p. para cima ou para baixo? Teste mercado, posição no Grupo C, "
"adversário 2F e team_context. Se nada mover, diga por quê com fonte."
```

Persist flag in transcript:

```python
turn["forced_exact_mc_challenge"] = True
```

- [ ] **Step 5: Run meeting tests**

```bash
uv run --with pytest python -m pytest tests/test_meeting.py -q -k challenger
```

- [ ] **Step 6: Commit**

```bash
git add worldcup_brazil/pipeline.py config/worldcup_brazil.example.json tests/test_meeting.py
git commit -m "feat: force challenger on exact MC echo consensus"
```

---

### Task 4: Opponent Room Must Produce Phase Top-2 Evidence, Not Just Title Agreement

**Files:**
- Modify: `worldcup_brazil/pipeline.py`
- Modify: `worldcup_brazil/post_template.py`
- Test: `tests/test_pipeline.py`, `tests/test_post_template.py`

**Why:** In run 846, the opponent room succeeded in one round, which is an improvement over prior failures. But it can be too shallow if it merely accepts `5.1%`. The room exists to improve the path, not to vote on title.

- [ ] **Step 1: Write failing test for missing phase coverage**

```python
def test_opponent_room_consensus_requires_phase_top_two_coverage():
    result = {
        "exit_status": "consensus",
        "meeting_transcript": [
            {
                "round": 1,
                "responses": [
                    {"agent": "GPT 5.5", "scenario_probabilities": {"16 avos: Japão": 32.6}},
                    {"agent": "Gemini Pro", "scenario_probabilities": {}},
                ],
            }
        ],
    }

    assert _opponent_room_phase_coverage(result) == {
        "16_avos": 1,
        "oitavas": 0,
        "quartas": 0,
        "semifinal": 0,
        "final": 0,
    }
    assert _opponent_room_has_sufficient_phase_coverage(result) is False
```

- [ ] **Step 2: Implement coverage helper**

```python
_PHASE_KEYWORDS = {
    "16_avos": ("16 avos", "r32", "round of 32"),
    "oitavas": ("oitavas", "r16", "round of 16"),
    "quartas": ("quartas", "quarter"),
    "semifinal": ("semifinal", "semi"),
    "final": ("final",),
}
```

Count unique opponents per phase from `scenario_probabilities` keys and response text.

- [ ] **Step 3: Convert shallow consensus to warning**

If opponent room consensus has less than 2 candidates in at least 4 phases, keep it usable but emit:

```text
"Sala lateral teve consenso, mas cobertura de adversários por fase ficou rasa; caminho principal usa MC como base e sala como validação qualitativa."
```

- [ ] **Step 4: Surface in post/audit**

Short post should not become technical. Add one sentence only if warning exists:

```text
"Cruzamentos: sala lateral validou o mapa, mas o ranking de adversários por fase segue ancorado no Monte Carlo."
```

- [ ] **Step 5: Run tests**

```bash
uv run --with pytest python -m pytest tests/test_pipeline.py tests/test_post_template.py -q -k opponent
```

- [ ] **Step 6: Commit**

```bash
git add worldcup_brazil/pipeline.py worldcup_brazil/post_template.py tests/test_pipeline.py tests/test_post_template.py
git commit -m "feat: require opponent-room phase coverage signal"
```

---

### Task 5: Perplexity Source-Relevance Repair Before Removal

**Files:**
- Modify: `worldcup_brazil/pipeline.py`
- Test: `tests/test_pipeline.py`

**Why:** Run 846 lost Perplexity before the room due to source relevance. A fifth model improves diversity if it can be cheaply repaired without lowering source standards.

- [ ] **Step 1: Write failing test for off-scope source repair eligibility**

```python
def test_source_planning_off_scope_sources_are_repairable_once():
    issue = _make_validation_issue(
        gate_name="source_planning_readiness",
        matched_rule="source_relevance",
        offending_excerpt="fontes de tipografia e design visual",
        field="source_urls",
        severity="blocking",
        recoverability="source",
        repair_hint="trazer fontes de odds, rankings e notícias de futebol competitivo",
    )

    assert _source_planning_issue_repair_class(issue) == "targeted_source_repair"
```

- [ ] **Step 2: Add targeted repair prompt**

Prompt:

```text
"Você foi removido porque interpretou 'fontes' como fontes visuais/tipográficas ou trouxe material fora de futebol competitivo. Refaça apenas o planejamento de fontes com odds, rankings, notícias de escalação/lesão, resultados e previews esportivos verificáveis. Não estime de novo se não tiver fonte."
```

- [ ] **Step 3: Bound it to one attempt**

Do not loop forever. One 60-90s attempt. If still off-scope, remove.

- [ ] **Step 4: Run tests**

```bash
uv run --with pytest python -m pytest tests/test_pipeline.py -q -k source_planning
```

- [ ] **Step 5: Commit**

```bash
git add worldcup_brazil/pipeline.py tests/test_pipeline.py
git commit -m "fix: repair off-scope source planning before removal"
```

---

### Task 6: Field-Level Salvage for Invalid Meeting Responses

**Files:**
- Modify: `worldcup_brazil/pipeline.py`
- Modify: `worldcup_brazil/models.py` if schema needs a new field
- Test: `tests/test_meeting.py`

**Why:** Run 846 removed one GPT response for fixed quanti/quali wording and one DeepSeek response for impossible opponent. The vote should stay removed, but valid evidence in the response can still be useful for future prompts and audit.

- [ ] **Step 1: Add schema fields**

If `AgentOpinion` supports dynamic attributes, use dict metadata. Otherwise add:

```python
evidence_usable: bool = False
numeric_vote_usable: bool = True
```

- [ ] **Step 2: Write failing test**

```python
def test_removed_response_can_keep_sources_but_not_numeric_vote():
    opinion = parse_agent_opinion({
        "self_identification": {"name": "DeepSeek V4 Pro"},
        "title_pct": 5.1,
        "answer": "Citou Holanda nas oitavas onde não era permitida.",
        "source_urls": ["https://example.com/odds"],
    })

    sanitized = _sanitize_main_meeting_opinions([opinion], config=bracket_config)

    assert sanitized[0].removed_from_main is True
    assert sanitized[0].numeric_vote_usable is False
    assert sanitized[0].evidence_usable is True
```

- [ ] **Step 3: Exclude from consensus math**

All consensus, spread and support calculations must use `not removed_from_main and numeric_vote_usable`.

- [ ] **Step 4: Include evidence only in audit**

Do not let salvaged evidence move the current run number. Store it for audit and next prompt context only.

- [ ] **Step 5: Commit**

```bash
git add worldcup_brazil/pipeline.py worldcup_brazil/models.py tests/test_meeting.py
git commit -m "feat: salvage evidence from invalid meeting responses"
```

---

### Task 7: Post Labels Must Distinguish "MC Ratified" vs "Models Repriced"

**Files:**
- Modify: `worldcup_brazil/post_template.py`
- Test: `tests/test_post_template.py`

**Why:** Run 846's most honest interpretation is: the models ratified the MC. The post should say that when true. If future runs are actually repriced by models, say that instead.

- [ ] **Step 1: Write failing tests**

```python
def test_post_says_models_ratified_mc_when_consensus_equals_mc():
    bundle = fixture_bundle(
        metadata={
            "agent_title_consensus_pct": 5.1,
            "monte_carlo": {"stage_probabilities": {"titulo": 5.1}},
            "numeric_chairman": {"stage_probability_blend": {"monte_carlo_weight": 0.6, "model_weight": 0.4}},
        }
    )

    text = render_template_post(bundle)

    assert "os modelos ratificaram o Monte Carlo" in text
```

```python
def test_post_says_models_repriced_when_consensus_differs_from_mc():
    bundle = fixture_bundle(
        metadata={
            "agent_title_consensus_pct": 6.2,
            "monte_carlo": {"stage_probabilities": {"titulo": 5.1}},
            "numeric_chairman": {"stage_probability_blend": {"monte_carlo_weight": 0.6, "model_weight": 0.4}},
        }
    )

    text = render_template_post(bundle)

    assert "os modelos moveram o funil" in text
```

- [ ] **Step 2: Implement label helper**

```python
def _numeric_decision_label(bundle: Any) -> str:
    metadata = getattr(bundle, "metadata", {}) or {}
    mc_title = (((metadata.get("monte_carlo") or {}).get("stage_probabilities") or {}).get("titulo"))
    model_title = metadata.get("agent_title_consensus_pct")
    if mc_title is None or model_title is None:
        return "Regra numérica: 60% Monte Carlo, 40% modelos."
    if abs(float(mc_title) - float(model_title)) <= 0.15:
        return "Regra numérica: 60% Monte Carlo, 40% modelos; hoje os modelos ratificaram o Monte Carlo."
    return "Regra numérica: 60% Monte Carlo, 40% modelos; hoje os modelos moveram o funil antes da publicação."
```

- [ ] **Step 3: Run template tests**

```bash
uv run --with pytest python -m pytest tests/test_post_template.py -q -k numeric
```

- [ ] **Step 4: Commit**

```bash
git add worldcup_brazil/post_template.py tests/test_post_template.py
git commit -m "feat: label MC ratification versus model repricing"
```

---

### Task 8: Final Validation and Run Audit

**Files:**
- No code files unless tests reveal regressions

- [ ] **Step 1: Run focused tests**

```bash
uv run --with pytest python -m pytest tests/test_pipeline.py tests/test_monte_carlo.py tests/test_meeting.py tests/test_post_template.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full validation**

```bash
make validate
```

Expected: all pytest tests pass, compileall passes, contracts pass.

- [ ] **Step 3: Run next daily smoke only when user authorizes cost**

```bash
make doctor
make force
```

Expected improvements in the next bundle:

- `market_title_challenge.market_low_pct` must not equal the model title if true market odds are present.
- `team_context_sensitivity` must exist in metadata.
- If model consensus equals MC for 2 rounds, transcript must show an exact-MC challenger question.
- Opponent room must report phase coverage or warn that path is MC-anchored.
- If Perplexity is removed again, warning must say targeted repair failed, not just "source invalid".
- Short post must disclose whether models ratified MC or moved the funil.

- [ ] **Step 4: Commit any test-only follow-up**

```bash
git status --short
git add .
git commit -m "test: cover run 846 assertiveness contracts"
```

---

## Priority Order

1. **Task 1: Structured market evidence** — highest value because it prevents the final title from being accepted below market without a clean external comparison.
2. **Task 2: Team-context sensitivity** — highest numerical value because it quantifies how much the Brazil context penalty moves the title.
3. **Task 3: Anti-echo challenger** — highest debate-quality value because it stops "MC ratified" from masquerading as independent judgment.
4. **Task 4: Opponent-room phase coverage** — improves bracket-path accuracy, especially 16 avos and oitavas.
5. **Task 5: Perplexity source repair** — improves model diversity if cheap; not worth weakening source standards.
6. **Task 6: Field-level salvage** — useful, but must be careful not to let invalid votes re-enter consensus math.
7. **Task 7: Post labels** — improves editorial honesty; lower numerical impact but high communication value.
8. **Task 8: Validation and audit** — required before any next run is trusted.

---

## Expected Improvement in the Next Run

This plan should not be sold as "Brazil probability goes up." The correct promise is sharper calibration:

- The title may move up, down, or stay near `5.1%`.
- The market comparison will become cleaner because it will stop treating the MC number as market evidence.
- The team-context penalty will become auditable through sensitivity, not just accepted as a single hidden delta.
- The model debate will become less passive because exact MC agreement triggers a challenger turn.
- The opponent path will be better qualified by phase coverage instead of only title agreement.
- The post will state whether the council validated the MC or actually repriced it.

The best success criterion for the next `make force` is not a higher title number. It is a bundle where every material number has a clear source: MC base, model adjustment, market challenge, and team-context sensitivity.

