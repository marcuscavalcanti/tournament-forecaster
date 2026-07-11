# Data Sources

## Results and bracket

- Source: official FIFA calendar API `https://api.fifa.com/api/v3/calendar/matches`
- Parameters: `idCompetition=17`, `idSeason=285023`, `language=en`, `count=500`
- Retrieved at: `2026-07-11T19:54:25Z`
- Checked-in data: normalized match facts, source IDs, schedule IDs, and team IDs only
- Raw API response: never checked in
- Final result types accepted: `1`, `2`, and `3`; type `3` is completed extra time
- Singular FIFA stage label `Quarter-final` maps to `quarter-finals`

The snapshot has 98 completed facts. Norway–England and Argentina–Switzerland were
not final at retrieval and are bracket fixtures, not completed results.

## Ratings

- Source: project-authored `team_ratings` seed frozen in git commit `a7b6e694`
- Exact git commit timestamp: `2026-06-09T23:27:23-03:00`
- Canonical ratings object SHA-256: `983a20748541db3612dd75fa2d5dde954d1b89de52a23c1b19f345a427bca259`

The ratings are leakage-free for the 72 group outcomes because they were frozen
before those matches. They are not an official FIFA rating source and do not prove
universal model calibration.

## Update procedure

```bash
python scripts/build_world_cup_2026_example.py --fetch   --output-dir examples/world-cup-2026-live
```

For deterministic fixture tests, use `--fixture PATH --retrieved-at TIMESTAMP`.
The builder rejects unknown teams, conflicting duplicate matches, invalid winners,
unsupported stages/result types, and any non-final row as a completed fact.
