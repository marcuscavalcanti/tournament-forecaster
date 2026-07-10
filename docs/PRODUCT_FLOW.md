# Product Flow

- **Status:** Target product contract for the open-source migration
- **Product:** Tournament Forecaster

Tournament Forecaster gives a user two entry paths: an offline quickstart that proves the product works in under five minutes, and an advanced path for forecasting a real competition with optional live data and multi-agent analysis.

```mermaid
flowchart TB
    U["User"] --> G{"What do you want to do?"}

    subgraph Entry["Choose an entry path"]
        direction LR
        Q["Quickstart: no keys or network"] --> S["Bundled synthetic tournament"]
        P["Choose a competition preset"] --> F["Choose the focus team"]
        I["Initialize a custom tournament"] --> F
        F --> D["Teams, ratings, fixtures, stages, and completed results"]
    end

    G -->|"See it work in under five minutes"| Q
    G -->|"Forecast a supported competition"| P
    G -->|"Model another tournament"| I

    S --> SQ["Use the documented seed and 10,000 iterations"]
    D --> L{"Use live data?"}
    L -->|"No"| LD["Use local validated data"]
    L -->|"Yes"| LP["Preview and synchronize results or odds"]

    SQ --> V["Validate schema, stage graph, freshness, and locked results"]
    LD --> V
    LP --> V

    V --> E["Simulate the complete tournament"]
    E --> C{"Enable the optional council?"}
    C -->|"No"| X["Keep the deterministic forecast"]
    C -->|"Yes"| R["Research sources and challenge bounded context"]
    R --> B["Apply only validated, bounded adjustments"]
    B --> X

    X --> O["Generate forecast outputs"]
    subgraph Outcomes["Inspect and use the result"]
        direction LR
        O1["Stage reach and title probability"]
        O2["Likely opponents and matchup probabilities"]
        O3["JSON, Markdown, SVG bracket, and audit trail"]
    end
    O --> O1
    O --> O2
    O --> O3

    O3 --> N{"A match was completed or an input changed?"}
    N -->|"Yes"| UP["Refresh facts, preserve locked results, and rerun"]
    UP --> V
    N -->|"No"| SHARE["Review, compare, or publish the forecast"]
```

## Product Principles

1. **Useful before configuration:** the first offline forecast requires no keys, network access, or manual file edits.
2. **One focus team, complete tournament:** the product highlights one team while simulating every match needed to preserve opponent and qualification probabilities.
3. **Facts before forecasts:** completed results are immutable and future probabilities are recalculated around them.
4. **Intelligence is optional:** the deterministic engine is the product; model research and debate are bounded enhancements.
5. **Every probability is inspectable:** users receive machine-readable results, a human report, a bracket, warnings, provenance, and an audit trail.
6. **The forecast is a loop:** new results and validated inputs produce a new run without rewriting tournament logic.

## Product Surfaces

| Surface | Primary user outcome |
| --- | --- |
| `quickstart` | Prove the installation and generate the first forecast offline |
| `init` and presets | Configure a tournament without writing Python |
| `validate` | Find structural or stale-data problems before simulation or paid calls |
| `update-results` and `update-odds` | Preview and ingest external facts safely |
| `simulate` | Estimate stage reach, matchups, and championship probability |
| optional council | Add sourced context and an auditable challenge to the deterministic baseline |
| `report` | Produce reusable JSON, Markdown, SVG, and audit artifacts |
