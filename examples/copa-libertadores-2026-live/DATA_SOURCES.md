# Data Sources

## Official competition facts

This snapshot normalizes the official 2026 Copa Libertadores round-of-16 field,
seed order, draw labels, bracket progression, and knockout format. It was retrieved
on `2026-07-13` from the following CONMEBOL sources:

- [Round-of-16 fixture and seed order (PDF)](https://www.conmebol.com/wp-content/uploads/2026/06/Fixture_8vos-de-Final_CONMEBOL-Libertadores-2026_5.6.2026-1.pdf)
- [Round-of-16 schedule](https://gol.conmebol.com/libertadores/pt-br/news/datas-e-horarios-assim-serao-disputadas-as-oitavas-de-final-da-conmebol-libertadores)
- [CONMEBOL Libertadores 2026 Club Manual (PDF)](https://cdn.conmebol.com/wp-content/uploads/2025/12/CL-2026-Manual-de-Clubes-POR-Fev26.pdf)

The manual defines the official downstream path as `A-H`, `B-G`, `C-F`, and `D-E`
in the quarter-finals; then `QF1-QF4` and `QF2-QF3` in the semi-finals. Round of
16, quarter-finals, and semi-finals are two-leg ties, with the better group-stage
seed hosting leg two. Aggregate draws before the final go directly to penalties;
the one-leg final uses extra time and then penalties.

## Transformation and boundaries

`tournament.json` contains an independently normalized field, seed map, and
bracket topology. It does not include CONMEBOL branding, logos, raw documents, or
provider payloads. Team ratings are project-authored synthetic inputs for a
reproducible forecast, not official rankings, odds, or a claim of predictive
accuracy.

This is a round-of-16 snapshot. It intentionally does not reconstruct the completed
group tables or CONMEBOL's competition-specific head-to-head ranking criteria.
Refresh the facts from the official sources before using this configuration after
the fixture or bracket changes.
