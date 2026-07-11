# FIFA World Cup 2026 Live Snapshot

This reproducible example starts from the official state retrieved at `2026-07-11T19:54:25Z`.
It retains all 48 teams, all 72 completed group matches, 16 completed Round of 32
matches, eight completed Round of 16 matches, and the two completed quarter-finals.
France is the default focus team and is already locked into the semi-finals.

Run offline after installing the package:

```bash
tournament-forecast simulate --config tournament.json --iterations 10000
tournament-forecast simulate --config tournament.json --focus-team spain --iterations 10000
tournament-forecast backtest --input backtest.json --output backtest-report.json --min-resolved 72
```

The third-place match is omitted because the generic bracket contract has no loser
entrant. Runtime forecast output directories are intentionally not checked in.
