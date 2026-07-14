# Copa Libertadores 2026 Round of 16 Snapshot

This ready-to-run example forecasts Palmeiras from the official 2026 Copa
Libertadores round of 16. The fixed `G` tie is Palmeiras versus Cerro Porteño;
Palmeiras has seed `11` and Cerro Porteño has seed `5`, so Cerro Porteño hosts the
second leg under the configured better-seed rule.

The field, seed order, bracket progression, and knockout rules are normalized from
official CONMEBOL sources recorded in [DATA_SOURCES.md](DATA_SOURCES.md). Ratings
are synthetic project inputs, not official ratings or betting odds. This is a
frozen, reproducible round-of-16 snapshot rather than a live feed.

From the repository root, install once and run entirely offline:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
tournament-forecast validate --config examples/copa-libertadores-2026-live/tournament.json
tournament-forecast simulate --config examples/copa-libertadores-2026-live/tournament.json --iterations 10000 --output-dir outputs
```

The forecast files are written below:

```text
outputs/copa-libertadores-2026-round-of-16/palmeiras/
```

To investigate another club in the same snapshot, change only the focus team:

```bash
tournament-forecast simulate --config examples/copa-libertadores-2026-live/tournament.json --focus-team flamengo --iterations 10000 --output-dir outputs
```

The configuration models the official knockout progression `A-H`, `B-G`, `C-F`,
and `D-E`; the final is a single match. It does not model group-stage standings,
third-place rules, or future schedule changes beyond this frozen snapshot.
