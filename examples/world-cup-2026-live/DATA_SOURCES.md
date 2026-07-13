# Data Sources

## Results and bracket

- Source: official FIFA calendar API `https://api.fifa.com/api/v3/calendar/matches`
- Parameters: `idCompetition=17`, `idSeason=285023`, `language=en`, `count=500`
- Retrieved at: `2026-07-13T12:21:03Z`
- Checked-in data: normalized match facts, source IDs, schedule IDs, and team IDs only
- Raw API response: never checked in
- Final result types accepted: `1`, `2`, and `3`; type `3` is completed extra time
- Singular FIFA stage label `Quarter-final` maps to `quarter-finals`

The snapshot has 100 completed facts. At retrieval,
`4/4` quarter-finals,
`0/2` semi-finals, and
`0/1` final were complete.

## Redistribution basis

- Terms reviewed: FIFA [Terms of Service](https://inside.fifa.com/terms-of-service), last reviewed `2026-07-13`.
- Status: this directory contains a project-authored normalized factual compilation of match identities, participants, kickoff times, stages, and final scores for reproducible non-commercial analysis.
- Scope: no raw FIFA API response, commentary, article text, photograph, audiovisual work, logo, competition emblem, or other expressive FIFA content is redistributed.
- License boundary: this factual snapshot is not covered by the MIT License. MIT covers the project-authored software, documentation, schemas, and synthetic presets; no FIFA license, endorsement, trademark right, or ownership of FIFA source content is claimed.
- Attribution: provider IDs and the source endpoint are retained so every normalized fact can be traced back to FIFA. Users who refresh or redistribute the dataset must review the then-current source terms and applicable database or contract rights for their jurisdiction.

The repository includes the minimal normalized facts, rather than FIFA's source database or response structure, to make the checked-in simulation and backtest reproducible while keeping the source-rights boundary explicit.

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
unsupported stages/result types, any non-final row as a completed fact, and completed
rows unless `retrieved_at` is strictly after kickoff. FIFA calendar rows do not expose
a trusted result-finalization timestamp, so this prevents at-or-before-kickoff
backdating but cannot prove when the provider first made a final result available.
