# Group Two-Leg Knockout Template

This complete, project-authored synthetic example has two home-and-away groups, fixed two-leg semi-finals, and a one-leg championship final. Keep every team, stage, and tie reference as a unique stable ASCII ID.

Completed results are locked by `match_id` and `leg`. For a two-leg tie, record each leg independently; the engine simulates only legs that remain missing.

List the copy-ready fixture IDs before adding a completed result:

```python
from pathlib import Path
from tournament_forecaster import list_group_fixtures, load_tournament

tournament = load_tournament(Path("tournament.json"))
for fixture in list_group_fixtures(tournament, "group-stage"):
    print(fixture.match_id, fixture.home_team_id, fixture.away_team_id)
```

Exactly one knockout stage uses `"terminal": "championship"`. A placement match may use `"terminal": "placement"`; it cannot change title probability.

Run the first checks from this directory:

```text
tournament-forecast validate --config tournament.json
tournament-forecast simulate --config tournament.json
```
