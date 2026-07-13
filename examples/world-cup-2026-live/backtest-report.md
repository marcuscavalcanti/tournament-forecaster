# World Cup 2026 Group-Stage Backtest

- Status: `ok`
- Resolved cases: `72`
- Model: `poisson-elo-v1`
- Ratings SHA-256: `983a20748541db3612dd75fa2d5dde954d1b89de52a23c1b19f345a427bca259`
- Home advantage: `0` rating points (neutral site)

| Metric | Model | Uniform baseline |
|---|---:|---:|
| RPS | 0.146838 | 0.231481 |
| Multiclass Brier | 0.498738 | 0.666667 |
| Natural log loss | 0.832030 | 1.098612 |
| Top-pick accuracy | 0.625000 | 0.333333 |

RPS is the mean squared cumulative error over the ordered outcomes home/draw/away,
divided by `K-1 = 2`. Brier is the unscaled sum of three squared class errors.
Log loss uses the natural logarithm. Model top-pick accuracy counts a case when the
observed class has the unique highest model probability. The uniform baseline uses
`1/3` for every class and an expected top-pick accuracy of `1/3`.
