# Adding A Provider

## Contract

A provider adapter converts one external contract into a small versioned local document. The deterministic engine consumes normalized facts, never a vendor response object. Acquisition, normalization, preview, and apply remain separate operations.

## Implementation Rules

1. Document credentials separately from non-secret source IDs.
2. Keep network clients and optional SDKs outside package runtime dependencies unless separately approved.
3. Accept a saved synthetic fixture in tests; CI must not call the provider.
4. Use allowlisted fields, strict timestamps, stable source IDs, and explicit stage-label mappings.
5. During acquisition, reject non-final results and any claimed result not observed after the authoritative kickoff before preview. The generic import schema deliberately has no schedule fields and cannot infer this ordering.
6. Reject unknown statuses, stages, teams, duplicate/conflicting facts, unsafe metadata, and secret-bearing URLs.
7. Preserve provider name, `retrieved_at`, source ID, and transformation warnings.
8. Provide preview output before any mutation and require an explicit apply action.
9. Fail closed on contract drift. Never guess a renamed field or stage.

Provider APIs are external contracts that may change. A passing saved-fixture test proves compatibility with that fixture, not the current live service. Before a release that refreshes data, inspect the official response and update normalization tests when the contract has legitimately changed.

## Review Checklist

- No key, cookie, authorization header, raw response, quota, or price is committed.
- Logs and errors redact credentials in userinfo, query parameters, and URL fragments.
- Local paths, symlinks, file replacement, and apply races are tested.
- Optional provider failure cannot corrupt tournament truth.
- Data rights, citations, retention, and deletion are documented.
- The provider name and any factual sample do not imply affiliation.
