# Evidence fusion and scoring model v0.1

## Purpose

The scoring core combines validated, normalized stock-level evidence from
independent research families. It is deliberately separate from source
collection and feature calculation: adapters must validate source semantics
before producing a family observation.

## Independent evidence families and provisional weights

| Evidence family | Short-term (1–4 weeks) | Positional (1–6 months) |
|---|---:|---:|
| Quarterly FPI ownership | 10 | 35 |
| Verified FPI disclosed deals | 15 | 15 |
| Cash-market behaviour (including peer-relative strength) | 35 | 35 |
| Stock derivatives | 40 | 15 |
| **Total** | **100** | **100** |

Market-wide FII/DII cash flows and participant-category aggregates are deliberately excluded from per-stock scoring. They will be reported as market-regime context alongside the rankings. These weights are research starting points, not empirically calibrated parameters.
They must be evaluated with historical walk-forward tests before production
rankings are treated as reliable.

## Observation contract

Every stock/family observation has:

- `family`: one configured independent evidence family
- `status`: `observed`, `unavailable` or `not_applicable`
- `score`: a normalized 0–1 family strength only when status is observed
- `source_refs`: provenance references for every observed score
- `reason`: mandatory explanation for unavailable or not-applicable states

A verified zero signal is represented as `observed` with score 0.0 and source
provenance. Missing data is `unavailable` with a null score. F&O is
`not_applicable` for a verified cash-only stock, but remains unavailable when
eligibility itself is unknown.

## Score and coverage

The horizon score is the weighted mean of observed families only:

`score = 100 × sum(weight × family_score) / sum(observed_family_weights)`

Coverage is reported separately:

`coverage = observed_family_weight / applicable_family_weight × 100`

Unavailable families reduce coverage but do not become negative evidence.
Not-applicable families are excluded from both the score and applicable-weight
denominator. A stock is rankable only when it has at least 60% coverage, at
least three observed families and a score of at least 45/100.

Each family contributes at most one normalized score per stock/horizon.
Price trend, volume, delivery and peer-relative strength are combined inside
the cash-market family; peer-relative strength is not a separate independent
family. This prevents correlated price features from inflating either the score
or the corroboration grade.

## Evidence grades

- **Grade 1 – Confirmed:** requires a separately verified, direct FPI
  transaction. A quarterly holding change, bulk/block deal without validated
  FPI identity, or market-wide derivatives statistic cannot qualify.
- **Grade 2 – Corroborated:** at least three independent evidence families
  each score 0.60 or higher.
- **Grade 3 – Early Watchlist:** positive but insufficiently corroborated
  evidence.
- **Unclassified:** insufficient positive evidence.

Evidence grade and numeric score are separate outputs.

## Attribution boundaries

- Quarterly FPI ownership is a reported holding at a date, not a dated purchase.
- Bulk/block deal counterparties are not automatically FPI.
- Stock-level futures/options are market-wide contract activity, not proof of
  FII positions.
- Participant-wise reports are category aggregates, not participant-by-stock
  positions.
- Daily FII/DII cash flow is market-wide context, not stock-specific flow.

Market-wide flows therefore remain context metadata and are not treated as
direct stock-level FPI transactions.

## Production gate

Model version 0.1 is provisional. Historical feature calculation, source
coverage, walk-forward backtesting, ranking stability and false-positive
analysis are required before enabling production recommendations.
