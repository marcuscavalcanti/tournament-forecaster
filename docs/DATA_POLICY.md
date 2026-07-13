# Data Policy

## Repository Data

The repository may contain:

- project-authored synthetic presets and fixtures;
- schemas and minimal examples needed to explain public contracts; and
- normalized, cited factual example data with a retrieval timestamp and documented transformation.

The World Cup 2026 example is allowed because it stores normalized fixture/result facts and cited provenance, not a raw provider response or protected visual asset.

The repository must not contain raw provider payloads, credentials, cookies, authorization headers, local `.env` files, personal absolute paths, browser captures, attachments, caches, runtime outputs, or protected provider and governing-body logos.

## Provenance Minimum

Every factual dataset must identify the source, `retrieved_at` timestamp, source IDs when available, normalization steps, rating provenance, redistribution basis, and known limitations. A citation is not an endorsement and does not transfer the source's rights.

## Acquisition And Retention

Acquire data outside the deterministic simulation. Store temporary raw responses only in an ignored, access-controlled local directory. Minimize fields immediately, remove secrets and request metadata, validate the normalized result, then delete the raw response according to the source terms and operational need.

## Corrections And Updates

Do not rewrite a completed-result fact silently. Preview additions and conflicts, retain retrieval provenance, and review changed external labels or schemas as contract drift. A refreshed snapshot should explain what changed and why. Generated forecasts belong in ignored output directories, not source control.

## Personal Data

Tournament facts should not require personal data beyond public sporting identities. Do not add private contact details, account identifiers, location traces, or behavioral profiles. Security reports containing sensitive information use GitHub Private Vulnerability Reporting and are not dataset inputs.
