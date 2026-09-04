# UEFA Champions League 2026/27: Real Madrid

This example builds a local tournament configuration from UEFA's published league-phase fixtures and forecasts Real Madrid from the 36-club league phase through the final.

```bash
python3 scripts/build_champions_league_2026_27_example.py \
  --output examples/champions-league-2026-27/tournament.local.json
tournament-forecast validate --config examples/champions-league-2026-27/tournament.local.json
tournament-forecast simulate --config examples/champions-league-2026-27/tournament.local.json \
  --iterations 10000 --output-dir outputs
```

The builder rejects anything other than 36 clubs, 144 fixtures, eight fixtures per club, and four home/four away fixtures. The generated file is local and ignored by Git because UEFA fixture data is acquired at runtime rather than redistributed.

The format includes the league table, restricted seeded knockout playoff and round-of-16 draws, two legs through the semifinals, a one-match final, no away-goals rule, and extra time then penalties. The frozen strength ratings are project-authored inputs, not official UEFA coefficients and not a claim of calibration.

Enable the optional council with `examples/champions-league-2026-27/council.example.json`; the deterministic engine retains ownership of standings, legal draw groups, bracket topology, and completed facts.

See [DATA_SOURCES.md](DATA_SOURCES.md) for provenance and limitations.

The Champions template contains five requested roles: Kimi, Gemini, Claude Opus 5, Codex, and DeepSeek. Copy it to `council.local.json`, replace every model placeholder with an ID available in your account, set `enabled` to `true`, export the named API-key variables, validate it, and add `--council-config council.local.json --council` to the simulation command. Local CLI authentication is intentionally not reused by the HTTPS council.
