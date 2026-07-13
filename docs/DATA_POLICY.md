# Data Policy

## Repository Data

The repository may contain:

- project-authored synthetic presets and fixtures;
- schemas and minimal examples needed to explain public contracts; and
- normalized, cited factual example data whose source redistribution license covers the checked-in use, including applicable database rights.

The World Cup 2026 example is allowed because its match facts come from OpenFootball's CC0 1.0 `worldcup.json` database. The directory notice pins the exact source URL, license URL, retrieval timestamp, source SHA-256, transformation, and known limitations. Normalization or a disclaimer alone is never a redistribution basis.

The repository must not contain raw provider payloads, credentials, cookies, authorization headers, local `.env` files, personal absolute paths, browser captures, attachments, caches, runtime outputs, or protected provider and governing-body logos.

## Provenance Minimum

Every factual dataset must identify the exact source and license URLs, `retrieved_at` timestamp, source SHA-256, stable identifier policy, transformation steps, rating provenance, redistribution basis, license boundary, and known limitations. The cited source license must actually permit repository redistribution; attribution, normalization, factuality, or a disclaimer is not a substitute. Source-derived facts retain their source license. The repository MIT license applies only to project-authored material and does not relicense third-party facts.

## Acquisition And Retention

Acquire data outside the deterministic simulation. Store temporary raw responses only in an ignored, access-controlled local directory. Minimize fields immediately, remove secrets and request metadata, validate the normalized result, then delete the raw response according to the source terms and operational need.

## Corrections And Updates

Do not rewrite a completed-result fact silently. Preview additions and conflicts, retain retrieval provenance, and review changed external labels or schemas as contract drift. A refreshed snapshot should explain what changed and why. Generated forecasts belong in ignored output directories, not source control.

## Personal Data

Tournament facts should not require personal data beyond public sporting identities. Do not add private contact details, account identifiers, location traces, or behavioral profiles. Security reports containing sensitive information use GitHub Private Vulnerability Reporting and are not dataset inputs.
