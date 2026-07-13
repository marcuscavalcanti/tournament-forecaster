# First-Class Multi-LLM Council Design

**Date:** 2026-07-13

**Status:** Approved

## Product Decision

The multi-LLM council is a first-class Tournament Forecaster capability, not a
legacy-only workflow. It remains optional so a fresh clone can simulate without
network access or provider credentials.

- Council disabled: the published forecast is 100% deterministic engine output.
- Council enabled and quorum reached: the published stage and championship
  probabilities use 55% deterministic engine output and 45% council consensus.
- Council enabled but unavailable, invalid, or below quorum: the run completes
  with the deterministic baseline and an explicit warning and audit status.

The deterministic engine always owns completed results, tournament topology,
legal opponents, matchup probabilities, and structural invariants. The council
may challenge forecast probabilities but cannot rewrite observed facts or the
bracket.

## Public Interface

Council settings live in a separate JSON document so tournament definitions stay
portable and credential-free. The generic CLI accepts:

```text
tournament-forecast simulate \
  --config tournament.json \
  --council-config council.local.json \
  --council
```

`--no-council` is a hard runtime override. Omitting `--council-config` keeps the
current offline behavior. A checked-in example documents all supported fields;
local copies use the already ignored `*.local.json` convention.

The council document contains:

- `enabled`, `engine_weight`, and `council_weight`;
- `rounds`, `minimum_valid_agents`, `timeout_seconds`, and `max_attempts`;
- an agent list with `id`, `display_name`, `provider`, `model`,
  `api_key_env`, provider endpoint when required, `reasoning_effort`,
  `thinking_budget_tokens`, `max_output_tokens`, and `temperature`.

The weights must be finite probabilities that sum to exactly 1 within a small
floating-point tolerance. The public example ships with 0.55 and 0.45.

## Debate Contract

Round 1 asks every available agent for an independent structured review of the
baseline. Round 2 provides anonymized valid positions to the same agents and asks
for a final position. A one-round configuration is supported for cost control,
but two rounds are the documented default because the debriefing is the product
differentiator.

Every valid opinion must include:

- every configured stage probability;
- the championship probability;
- confidence in the opinion;
- a concise summary and key factors.

The runner rejects non-finite values, missing stages, probabilities outside
`[0, 1]`, non-monotonic reach funnels, championship probability above final-stage
reach, and changes to deterministic stage values already locked at 0 or 1.

Consensus is the per-field median of valid final-round opinions. A council result
is usable only when `minimum_valid_agents` distinct agents produce valid final
opinions. Individual models never receive extra voting weight.

## Provider Boundary

The initial generic adapters use direct HTTPS APIs and Python's standard library:

- OpenAI Responses API;
- Anthropic Messages API;
- OpenAI-compatible chat completions, including Perplexity and DeepSeek; and
- Google Gemini generateContent.

API keys are read only from the configured environment-variable names. Secret
values, authorization headers, and raw provider payloads are never persisted.
Provider failures retain a bounded, sanitized response detail. Quota and billing
errors, including Gemini prepayment depletion, are surfaced clearly in council
metadata and warnings.

Local shell or browser-command bridges are intentionally outside this generic
contract. The legacy workflow may retain explicitly enabled bridges during its
deprecation window.

## Blending And Uncertainty

For each probability `p`, the published value is:

```text
published = 0.55 * engine + 0.45 * council
```

The final confidence interval treats council consensus as fixed and carries only
the Monte Carlo sampling component:

```text
lower = 0.55 * engine_lower + 0.45 * council
upper = 0.55 * engine_upper + 0.45 * council
```

Artifacts label that limitation explicitly. The council metadata preserves the
engine baseline, council consensus, weights, agent statuses, round positions,
summaries, and the reason for fallback when no blend was applied. Matchup
probabilities remain engine-only and are labeled as such.

## Reporting And Failure Semantics

`forecast.json` contains a structured `council` object whether the configured
council is disabled, applied, or degraded. Markdown includes a Council Debrief
section with configuration, participants, debate positions, consensus, blend,
and failures. The terminal summary states `Council: disabled`, `applied`, or
`fallback`.

Provider and parse failures do not invalidate a structurally sound deterministic
forecast. Configuration errors fail before network calls. Output publication
remains atomic.

## Documentation Positioning

The README leads with the hybrid product: deterministic tournament simulation
plus an optional, auditable multi-LLM council. Quickstart remains offline. A
second runnable path shows how to copy the council example, configure models and
effort, supply environment keys, validate the council config, enable it, and
disable it again with one flag.
