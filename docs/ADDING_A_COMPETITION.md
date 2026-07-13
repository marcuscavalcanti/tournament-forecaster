# Adding A Competition

## 1. Choose The Closest Contract

Start with `tournament-forecast init` and one of the packaged templates. Use a group template for round-robin groups, a league template for explicit league fixtures, or the two-leg template when non-final knockout rounds use home-and-away ties.

## 2. Model Rules Explicitly

Assign stable IDs to teams, stages, fixtures, and ties. Declare direct and additional qualifiers, pairing mode, legs, home/away order, tie-break behavior, and the terminal championship stage. Do not encode competition logic in display names.

If the real competition uses an unsupported ranking rule, reseeding rule, draw constraint, or multi-phase transition, add and test that engine contract before publishing a forecast. Do not approximate it with a superficially similar preset.

## 3. Record Provenance

Add a `DATA_SOURCES.md` beside public repository data. Cite the source and retrieval time for factual fixtures and results. Explain rating construction, licensing or redistribution basis, normalization, and known limitations. Raw provider responses do not belong in the repository.

## 4. Validate Behavior

```bash
tournament-forecast validate --config path/to/tournament.json
tournament-forecast simulate --config path/to/tournament.json --iterations 1000 --output-dir outputs
```

Add deterministic tests for format topology, qualifier counts, completed-result locking, legal pairings, stable output IDs, and packaged/root parity when the competition becomes a preset. Use low iteration counts for structural tests and a documented production count for published artifacts.

## 5. Review Public Claims

State whether data is synthetic, normalized snapshot data, or locally acquired. Distinguish implemented behavior from future adapters. Do not imply governing-body affiliation or bundle protected logos.
