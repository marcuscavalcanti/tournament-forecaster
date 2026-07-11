# Group Knockout Template

This complete, project-authored synthetic example has two round-robin groups, one-leg semi-finals, and a one-leg championship final. Replace the stable ASCII IDs only with unique IDs; display names are presentation data.

Completed results are locked by `match_id` and `leg`. Add a completed result only for the configured stage and fixture identity; a later simulation preserves it rather than replacing it with a generated score.

List the copy-ready fixture IDs before adding a completed result:

```python
from pathlib import Path
from tournament_forecaster import list_group_fixtures, load_tournament

tournament = load_tournament(Path("tournament.json"))
for fixture in list_group_fixtures(tournament, "group-stage"):
    print(fixture.match_id, fixture.home_team_id, fixture.away_team_id)
```

Exactly one knockout stage uses `"terminal": "championship"`. A placement match may use `"terminal": "placement"`; it never supplies the title probability.

Run the first checks from this directory:

```text
tournament-forecast validate --config tournament.json
tournament-forecast simulate --config tournament.json
```
