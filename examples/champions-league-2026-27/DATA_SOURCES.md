# Data sources

- Official fixture page: https://www.uefa.com/uefachampionsleague/fixtures-results/
- Structured match endpoint used by that page: https://match.uefa.com/v5/matches?competitionId=1&seasonYear=2027&offset=0&limit=500
- Season: 2026/27 (`seasonYear=2027`)
- Acquisition: runtime only; the fetched UEFA payload and generated tournament file are not committed.

The builder selects records labeled `League Phase` and validates the complete 36-club, 144-match schedule. Club names and fixtures come from UEFA. Ratings are frozen project-authored modeling inputs and are deliberately separated from official facts. Re-run the builder to refresh source data, then review the generated diff locally before using it operationally.
