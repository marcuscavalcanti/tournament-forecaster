# Providers

Providers are acquisition boundaries, not owners of tournament truth. Tournament Forecaster validates local normalized files and remains offline by default. Provider APIs are external contracts that may change without notice; inspect upstream behavior and rerun contract tests before relying on a refreshed adapter.

## Values With Different Security Properties

- **Credentials** such as `THE_ODDS_API_KEY` are secrets. Load them from an environment variable or secret manager, require rotation after suspected exposure, require revocation when retired, and never commit or log them.
- **Competition and season IDs** identify public datasets and are not secrets. The World Cup example uses competition `17` and season `285023`.
- **Local bridges** are a reserved future extension and are not implemented by the generic CLI. No public environment variable enables one; any later bridge requires a separate threat model, explicit configuration contract, and security review.

## Platform Boundary

In `v0.1.0`, race-resistant results apply and durable report publication require POSIX file descriptors, no-follow opens, and native rename primitives. They are supported natively on macOS and Linux. Native Windows is not supported; run the CLI inside WSL2 and use Linux paths.

## Official FIFA Calendar Discovery

FIFA does not provide this project with a stable SDK contract. To locate the calendar request, open the official FIFA competition calendar in a browser, open developer tools, select the **Network** panel, filter Fetch/XHR traffic, and change a date or stage filter. Inspect the official-origin JSON request that carries competition and season parameters. For the checked-in example, verify competition `17` and season `285023`, response status, content type, pagination, stage labels, and retrieval time before normalization.

Do not hard-code an observed host, path, quota, price, or response shape into public documentation as permanent. Save only the normalized facts and citations needed by the example. Raw FIFA responses remain local and ignored.

Upstream stage labels are normalized before matching local stage IDs. Adapters must handle known singular/plural variants such as `Quarter-final` and `Quarter-finals`, while rejecting unknown labels. Never silently map a new external label to the nearest stage.

## Results: Preview Then Apply

Normalize a provider response into the documented local JSON or CSV import schema, then preview it:

```bash
tournament-forecast update-results --config tournament.json --source normalized-results.json
```

The preview separates additions, idempotent facts, conflicts, and unmatched rows. Resolve every blocker and inspect provenance before mutation. Apply the same unchanged source explicitly:

```bash
tournament-forecast update-results --config tournament.json --source normalized-results.json --apply
```

Conflict replacement requires the separate `--replace-conflicts` flag. Apply fails closed if the source or destination identity changes after preview, if a symlink substitution is detected, or if normalized facts are unknown, malformed, conflicting, or non-final.

The generic preview/apply layer validates only normalized final facts; it does not infer a schedule or decide whether a claimed result is temporally possible. Acquisition adapters must reject non-final results and any result not observed after the authoritative kickoff before producing the local import file. The checked-in OpenFootball example builder implements that temporal guard in `scripts/build_world_cup_2026_example.py` by requiring `retrieved_at` to be after `kickoff_at` before it emits a completed fact.

A successful mutation prints the retained backup path. Treat that path as manual rollback evidence: inspect the canonical config and remove the containing recovery directory only after reconciliation. The adjacent writer-lock file is persistent coordination state. Missing native exchange or directory-descriptor primitives fail before apply setup; a filesystem can still reject a native exchange at runtime, in which case the canonical config is preserved and the error reports every retained candidate, recovery, and lock path known to the operation.

## Odds

Obtain an API key directly from The Odds API and expose it only as `THE_ODDS_API_KEY` during the separate acquisition step. Consult the provider's current terms and documentation instead of relying on frozen quota or pricing statements here. Rotate and revoke keys through the provider account when access changes or exposure is suspected.

Normalize acquired odds into a local provenance document, then inspect it without changing deterministic probabilities:

```bash
tournament-forecast update-odds --source normalized-odds.json
```

Unknown fields, malformed timestamps, invalid decimal odds, unsafe metadata, and non-HTTP(S) source URLs fail closed. Missing optional odds data leaves the deterministic core available; an operator-defined required acquisition step should fail its own workflow rather than fabricate evidence.

## Raw Payload Policy

Never commit raw provider payloads, request headers, cookies, tokens, browser captures, or unredacted URLs. Credential-shaped query and fragment parameters are redacted before a provider URL is persisted. Keep temporary responses under an ignored `raw_provider_payloads/` directory, restrict access, and delete them after producing a minimal normalized artifact. Repository examples may include normalized factual data only when their source, retrieval timestamp, transformation, and redistribution basis are documented.
