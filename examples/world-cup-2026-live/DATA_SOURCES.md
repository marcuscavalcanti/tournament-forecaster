# Data Sources

## Results and bracket

- Source: OpenFootball `worldcup.json` World Cup 2026 snapshot
- Source commit: `056c53ec82feb3fb68da63d1ce74ec59fc23e95d`
- Exact source URL: https://raw.githubusercontent.com/openfootball/worldcup.json/056c53ec82feb3fb68da63d1ce74ec59fc23e95d/2026/worldcup.json
- Retrieved at: `2026-07-13T16:35:34Z`
- Source SHA-256: `b0aef8771d7fc3b6a5ec04cf7a9f9cd167c4e8b0be9152b3a35ae5629bb4e8d5`
- Source rows: `104`; completed rows at retrieval: `100`
- Source license: `CC0 1.0 Universal`
- Exact license URL: https://raw.githubusercontent.com/openfootball/worldcup.json/056c53ec82feb3fb68da63d1ce74ec59fc23e95d/LICENSE.md
- License scope: the repository license expressly addresses extraction, dissemination,
  reuse of data, and database rights.

At retrieval, `4/4` quarter-finals,
`0/2` semi-finals, and
`0/1` final were complete.

## Transformation

The deterministic builder validates all 104 rows and the chronological completion
frontier, maps OpenFootball labels to project team and stage IDs, converts each explicit
`UTC+/-H` kickoff offset to UTC, and retains only participants, stages, kickoff times,
and final match facts. For knockout matches, `et` is the final score when present;
`p` selects the winner but does not replace the tied football score. Goal events,
half-time scores, grounds, and the third-place tie are omitted. Existing numeric
knockout IDs are project-owned stable topology IDs, not source or provider IDs.

## Redistribution and license boundary

The normalized OpenFootball-derived match facts in `tournament.json` and
`backtest.json` retain CC0 1.0 status. The repository MIT license covers only
project-authored code, schemas, documentation, topology, transformations, synthetic
data, and ratings; it does not relicense the CC0 facts. The source and license links
remain recorded for reproducibility even though CC0 does not require attribution.

## Ratings

- Source: project-authored `team_ratings` seed frozen in git commit `a7b6e694`
- Exact git commit timestamp: `2026-06-09T23:27:23-03:00`
- Canonical ratings object SHA-256: `983a20748541db3612dd75fa2d5dde954d1b89de52a23c1b19f345a427bca259`

The ratings are leakage-free for the 72 group outcomes because they were frozen
before those matches. They are not an official rating source and do not prove
universal model calibration.

## Known limitations

- OpenFootball is a community-maintained dataset, not an official live feed.
- The source does not include a trusted result-finalization timestamp. The builder
  requires `retrieved_at` to be after `kickoff_at`, but that cannot establish when a
  score first became final.
- The generic bracket cannot represent third-place loser entrants, so match 103 is
  verified against source topology but omitted from the distributable tournament.
- Team aliases are explicit and fail closed when source labels drift.

## Update and verification procedure

Use an ignored local source capture. For the checked-in frontier:

```bash
python scripts/build_world_cup_2026_example.py \
  --source /private/tmp/openfootball-worldcup-2026.json \
  --retrieved-at 2026-07-13T16:35:34Z \
  --expected-source-sha256 b0aef8771d7fc3b6a5ec04cf7a9f9cd167c4e8b0be9152b3a35ae5629bb4e8d5 \
  --expected-completed-facts 100 \
  --output-dir examples/world-cup-2026-live

python scripts/build_world_cup_2026_example.py \
  --source /private/tmp/openfootball-worldcup-2026.json \
  --retrieved-at 2026-07-13T16:35:34Z \
  --expected-source-sha256 b0aef8771d7fc3b6a5ec04cf7a9f9cd167c4e8b0be9152b3a35ae5629bb4e8d5 \
  --expected-completed-facts 100 \
  --output-dir examples/world-cup-2026-live \
  --verify
```

For a future refresh, `--fetch` downloads the exact source URL above. Review the new
hash and frontier before replacing the checked-in artifacts. The updater rejects
unknown teams or stages, malformed offsets and scores, duplicate match numbers,
invalid extra-time or penalty outcomes, topology drift, and completion gaps.
