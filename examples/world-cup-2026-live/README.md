# FIFA World Cup 2026 Live Snapshot

This reproducible example starts from the official state retrieved at `2026-07-13T12:21:03Z`.
It retains all 48 teams and 100 completed match facts:

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
entrant. Runtime forecast output directories are intentionally not checked in.
