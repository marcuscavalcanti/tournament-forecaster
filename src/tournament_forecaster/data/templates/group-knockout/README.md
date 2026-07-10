# Group Knockout Template

This complete, project-authored synthetic example has two round-robin groups, one-leg semi-finals, and a one-leg championship final. Replace the stable ASCII IDs only with unique IDs; display names are presentation data.

Completed results are locked by `match_id` and `leg`. Add a completed result only for the configured stage and fixture identity; a later simulation preserves it rather than replacing it with a generated score.

Exactly one knockout stage uses `"terminal": "championship"`. A placement match may use `"terminal": "placement"`; it never supplies the title probability.

Run the first checks from this directory:

```text
tournament-forecast validate --config tournament.json
tournament-forecast simulate --config tournament.json
```
