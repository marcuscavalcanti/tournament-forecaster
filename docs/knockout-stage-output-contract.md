# Output Contract: Knockout Stage

This compatibility contract applies once Brazil enters the knockout stage. It documents the deprecated Brazil-specific renderer; the generic Tournament Forecaster remains the primary public product.

## Timeline

- Completed stages no longer appear as part of the future path.
- Completed results appear as narrative history before the remaining path.
- The next match is Brazil's first future fixture, using the date in the input bundle.
- A knockout heading compares `Brazil advances / opponent advances`; the draw value is always `0`.

## Path

- The road-to-title block starts at the current or next stage.
- If Brazil has completed the round of 32, the path starts at the round of 16.
- Historical context states explicitly how Brazil reached that point, including opponent and score.
- A locked matchup uses `Confirmed` and `100% chance of this matchup`.

## Tournament Run Summary

- Stage-reach probabilities come from Monte Carlo simulation conditioned on known results.
- When the next match is in the round of 16, quarter-final reach equals the probability of advancing from that match.
- For example, if `Brazil vs Norway` is `74.1%`, the summary reports `quarter-finals: 74%`; it does not substitute a post-debate value.
- The title percentage remains the final published probability from the run funnel.

## Analysis Notes

- Notes must not be generic room choreography, leadership language, or administrative narration.
- Notes expose substantive audit reasoning: a variable, threshold, trade-off, or tested hypothesis.
- Useful examples include:
  - `To move the title probability from 11.7% to 12.7%, Brazil vs Norway would need to rise from 74.1% to 80.4%.`
  - `The analysis defined triggers for odds, injury, lineup, or rating evidence that would move Brazil vs Norway or England by 3 percentage points.`
  - `Haaland's ankle status should become a sensitivity test, not an unsupported guess.`
- Percentages for different events must not be described as though one directly adjusted the other. A title probability of `11.7%` and a Brazil-vs-Norway advancement probability of `74.1%` are different quantities.

## Infographic

- The infographic uses the same temporal contract as the report.
- The latest-run panel shows the next future fixture, not an earlier resolved knockout match.
- A locked-matchup insight points to the next relevant confirmed fixture.
- Model ranking, run influence, and accuracy indicators may remain, but they must not present a resolved fixture as current.
