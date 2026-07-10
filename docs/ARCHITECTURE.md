# Technical Architecture

- **Status:** Target architecture contract for the open-source migration
- **Product:** Tournament Forecaster

The architecture keeps tournament rules and probability computation deterministic and offline. Network providers, model providers, local executable bridges, and publishing templates are adapters around that core, not owners of tournament truth.

## Component Architecture

```mermaid
flowchart TB
    USER["User or automation"] --> CLI["CLI: quickstart, init, validate, simulate, report, update"]
    CLI --> RUN["Run context: run_id, seed, cache policy, and provenance"]

    subgraph INPUTS["1. Two input classes"]
        direction LR
        subgraph TRUTH["Tournament truth inputs"]
            direction TB
            CFG["Tournament config or preset"]
            COMPATIN["Legacy worldcup_brazil adapter"]
            LOCAL["Ratings and completed-result ledger"]
            RESULTSP["Optional results: FIFA, JSON, or CSV"]
            FACTS["Normalized tournament facts"]
            CFG --> FACTS
            COMPATIN --> FACTS
            LOCAL --> FACTS
            RESULTSP -->|"Preview final results"| FACTS
        end

        subgraph EVIDENCE_INPUTS["Optional evidence inputs"]
            direction TB
            ODDSP["Odds provider"]
            LLMP["Model providers"]
            BRIDGE["Opt-in executable bridges"]
            EVIDENCE["Sourced external evidence"]
            ODDSP --> EVIDENCE
            LLMP --> EVIDENCE
            BRIDGE --> EVIDENCE
        end
    end

    subgraph EXECUTION["2. Execution paths"]
        direction LR
        subgraph CORE["Authoritative offline path"]
            direction TB
            LOAD["Load and normalize schema versions"]
            SCHEMA["Schema and semantic validation"]
            GRAPH["Stage graph and entrant validation"]
            FRESH["Freshness, fixture, alias, and conflict gates"]
            DOMAIN["Normalized domain model"]
            STAGES["Groups, league table, and knockout stages"]
            TABLES["Standings, tiebreakers, and qualification"]
            PAIR["Pairing, seeding, bracket, and two-leg rules"]
            MATCH["Match probability and score model"]
            MC["Seeded full-tournament Monte Carlo"]
            COHERENCE["Probability and locked-result invariants"]
            LOAD --> SCHEMA --> GRAPH --> FRESH --> DOMAIN
            DOMAIN --> STAGES --> TABLES --> PAIR --> MC --> COHERENCE
            DOMAIN --> MATCH --> MC
        end

        subgraph INTEL["Optional bounded intelligence path"]
            direction TB
            SECURITY["Credential redaction and bridge policy"]
            PLAN["Source plan and evidence collection"]
            SIDE["Opponent and path analysis"]
            COUNCIL["Main multi-agent council"]
            CONSENSUS["Validation, repair, and structured consensus"]
            SECURITY --> PLAN --> SIDE --> COUNCIL --> CONSENSUS
        end
    end

    FACTS --> LOAD
    EVIDENCE --> SECURITY
    RUN --> LOAD
    RUN --> SECURITY
    COHERENCE -. "Legal opponents, locked results, and bounds" .-> CONSENSUS

    COHERENCE -->|"Deterministic baseline"| CHAIR["Configured forecast finalizer"]
    CONSENSUS -->|"Optional bounded context"| CHAIR

    subgraph OUTPUT["3. Versioned artifacts"]
        direction LR
        FORECAST["Neutral forecast schema"]
        JSON["forecast.json"]
        MD["report.md"]
        SVG["bracket.svg"]
        AUDIT["audit.md and watchdog"]
        COMPATOUT["Legacy artifact emitter"]
        PUBLISH["Optional publishing templates"]
        FORECAST --> JSON
        FORECAST --> MD
        FORECAST --> SVG
        FORECAST --> AUDIT
        FORECAST --> COMPATOUT
        FORECAST --> PUBLISH
    end
    CHAIR --> FORECAST
```

## One Forecast Run

```mermaid
sequenceDiagram
    actor User
    participant CLI as tournament-forecast
    participant Validator as Offline validator
    participant Provider as Optional data provider
    participant Ledger as Completed-result ledger
    participant Engine as Tournament engine
    participant Council as Optional council
    participant Reporter as Reporters

    User->>CLI: quickstart or simulate(config, focus team)
    CLI->>Validator: Load and validate schema and stage graph
    Validator-->>CLI: Normalized tournament or actionable failure

    opt Live refresh explicitly enabled
        CLI->>Provider: Fetch results or odds with redacted settings
        Provider-->>CLI: External records or expected unavailability
        CLI->>Validator: Normalize teams, stages, dates, and status
        Validator->>Ledger: Atomically apply reviewed final results
    end

    CLI->>Ledger: Read immutable completed results
    CLI->>Engine: Simulate all remaining matches with a fixed seed
    Engine->>Engine: Standings, qualification, pairing, and knockout progression
    Engine-->>CLI: Deterministic forecast plus invariants

    opt Council enabled and provider-ready
        CLI->>Council: Baseline, legal opponents, sources, and adjustment bounds
        Council->>Council: Research, debate, repair, and structured consensus
        Council-->>CLI: Auditable bounded context or degraded no-op
    end

    CLI->>Reporter: Versioned forecast, provenance, warnings, and council audit
    Reporter-->>User: JSON, Markdown, SVG bracket, and audit artifacts
```

## Ownership Rules

| Concern | Owning component | Components that may not override it |
| --- | --- | --- |
| Completed match facts | validated result ledger | council, odds provider, publisher |
| Tournament topology | stage graph and pairing engine | council, result provider, templates |
| Standings and qualification | deterministic stage engine | council, renderer |
| Published probabilities | seeded simulation plus configured blend policy | individual model response |
| Contextual evidence | optional council and providers | deterministic core when council is disabled |
| Human presentation | report and publishing adapters | core domain model |

## Failure Behavior

- Invalid schemas, impossible stage references, stale required results, and result conflicts fail before simulation or paid model calls.
- Expected provider unavailability follows the configured `required`, `cached_with_ttl`, or `best_effort` policy; internal programming errors are never converted into provider downtime.
- Council failure degrades to the validated deterministic baseline. It never unlocks completed results, changes the stage graph, or invents legal opponents.
- Every accepted external fact records provider provenance and retrieval time. Every artifact records the `run_id`, input provenance, warnings, and compatibility conversions.
