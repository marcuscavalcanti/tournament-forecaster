# FIFA World Cup 2026 Live Snapshot

This reproducible example uses the [OpenFootball World Cup 2026 JSON](https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json)
snapshot retrieved at `2026-07-13T16:35:34Z` with source SHA-256 `b0aef8771d7fc3b6a5ec04cf7a9f9cd167c4e8b0be9152b3a35ae5629bb4e8d5`. The imported
match facts remain available under the source repository's CC0 1.0 license. It retains
all 48 teams and 100 completed match facts:

- `72/72` group-stage matches
- `16/16` Round of 32 matches
- `8/8` Round of 16 matches
- `4/4` quarter-finals
- `0/2` semi-finals
- `0/1` final

France is the default focus team and is already locked into the semi-finals.

Run offline after installing the package:

```bash
tournament-forecast simulate --config tournament.json --iterations 10000
tournament-forecast simulate --config tournament.json --focus-team spain --iterations 10000
tournament-forecast backtest --input backtest.json --output backtest-report.json --min-resolved 72
```

The third-place match is omitted because the generic bracket contract has no loser
entrant. Runtime forecast output directories are intentionally not checked in. The
repository MIT license does not relicense the CC0 match facts.
