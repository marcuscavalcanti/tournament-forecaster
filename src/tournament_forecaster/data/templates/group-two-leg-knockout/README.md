# Group Two-Leg Knockout Template

This complete, project-authored synthetic example has two home-and-away groups, fixed two-leg semi-finals, and a one-leg championship final. Keep every team, stage, and tie reference as a unique stable ASCII ID.

Completed results are locked by `match_id` and `leg`. For a two-leg tie, record each leg independently; the engine simulates only legs that remain missing.

Exactly one knockout stage uses `"terminal": "championship"`. A placement match may use `"terminal": "placement"`; it cannot change title probability.

Run the first checks from this directory:

```text
tournament-forecast validate --config tournament.json
tournament-forecast simulate --config tournament.json
```
