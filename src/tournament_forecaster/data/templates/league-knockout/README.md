# League Knockout Template

This complete, project-authored synthetic example uses an explicit league fixture list, ranking-based entrants, seeded two-leg quarter-finals, two-leg semi-finals, and a one-leg championship final. Stable ASCII IDs drive every reference; display names can change without changing the bracket.

Completed results are locked by `match_id` and `leg`. Keep each result attached to its configured fixture or tie; simulations generate only the missing legs.

Exactly one knockout stage uses `"terminal": "championship"`. A placement match may use `"terminal": "placement"`; it is excluded from championship probability.

Run the first checks from this directory:

```text
tournament-forecast validate --config tournament.json
tournament-forecast simulate --config tournament.json
```
