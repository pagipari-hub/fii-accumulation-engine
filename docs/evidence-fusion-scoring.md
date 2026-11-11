# FII accumulation scoring model v0.2 (provisional specification)

## Objective

The primary question is: **which Nifty 500 stocks show credible, persistent, stock-specific evidence consistent with FPI accumulation?**

The model deliberately separates three outputs:

1. **FII Accumulation Score (0–100):** the primary ranking of stock-specific FPI accumulation evidence.
2. **Market Confirmation Score (0–100):** independent price, delivery and volume confirmation. It never substitutes for FPI evidence.
3. **Catalyst assessment (0–10):** a separately displayed assessment of a visible, sourced reason the business may be attracting attention. It is not added to the FII score and cannot upgrade the FPI evidence grade.

This is a research specification only. It is not enabled for production ranking and its weights are not historically calibrated.

## 1. Primary FII Accumulation Score

| Dimension | Weight | Evidence required |
|---|---:|---|
| Verified net FPI transactions | 35 | Temporally verified FPI identity and reconciled disclosed buys/sells |
| Persistence | 25 | Positive net disclosed FPI activity across distinct weekly windows |
| Quarterly FPI ownership change | 20 | Comparable, identity-verified Category I/II ownership filings |
| Independent FPI buyer breadth | 10 | Distinct verified beneficial-owner groups, not duplicate legal-entity aliases |
| Relative accumulation intensity | 10 | Verified net disclosed buying relative to stock liquidity |
| **Total** | **100** | |

For observed dimensions only:

`FII score = 100 × Σ(weight × normalized_dimension) / Σ(observed_dimension_weights)`

`Coverage = Σ(observed_dimension_weights) / Σ(applicable_dimension_weights)`

Unavailable dimensions are excluded from the score denominator and reduce coverage. They are never converted to zero. A zero is valid only when the relevant source channel is complete and explicitly reports no qualifying activity for its defined scope.

### Direct-transaction evidence gate

A transaction can contribute to verified-direct FPI evidence only when:

- the counterparty is verified as an FPI for the transaction date;
- registration validity covers the transaction date;
- disclosed buy and sell sides are reconciled for the disclosed-event scope;
- source completeness, duplicate handling and the stock/ISIN join are validated.

A match against a later registry snapshot with unknown registration start date is an **investigation lead**, not verified historical FPI activity. Unmatched counterparties remain unresolved; gross purchases are not net accumulation.

Persistence means repeated positive net disclosed activity in distinct weekly windows. It does not imply that every FPI trade is publicly disclosed. The report must state the scope and limitations of the disclosure channel.

Quarterly ownership change is a reported holding change between filing dates, not a dated purchase. It is supporting evidence, not proof of the exact timing or reason for buying.

### Rankability and evidence grades

A score is eligible for a research ranking only with at least 60% applicable-weight coverage and at least three observed dimensions, plus an exact verified universe identity join. These are provisional minimums, subject to validation.

The numeric score and evidence grade are separate. A high numerical score cannot bypass the direct-transaction gate. Reports must distinguish:
- **Verified direct FPI evidence:** all temporal identity and transaction reconciliation checks pass.
- **Ownership-supported evidence:** verified quarterly FPI holding change, without dated transaction proof.
- **Corroborated / early watch:** supporting signals exist but direct attribution is incomplete.
- **Unverified lead:** current-registry or unresolved counterparty evidence; excluded from verified-direct scoring.

## 2. Market Confirmation Score

| Dimension | Weight | What it measures |
|---|---:|---|
| Delivery strength | 35 | Delivery participation and strengthening versus its own rolling baseline |
| Relative price strength | 30 | Stock return and market/sector-relative strength |
| Volume confirmation | 20 | Valid cash-market volume/turnover versus rolling baseline |
| Trend stability | 15 | Trend persistence, drawdown and volatility behaviour |
| **Total** | **100** | |

The same weighted-mean and coverage approach applies. Correlated features must be combined within their dimension. This score is displayed beside the FII score, never blended into it.

Stock futures/options activity can be shown as contextual market behaviour. Participant-category aggregate positioning is market-wide context only. Neither proves that FIIs hold or bought a particular stock.

## 3. Catalyst assessment (0–10, separate)

Record a visible reason only when supported by a dated source. Candidate categories include:
- earnings or margin improvement;
- order wins, business growth or capacity expansion;
- sector or policy change;
- corporate action, acquisition or restructuring;
- index inclusion or other institutional event;
- material valuation or balance-sheet change.

Suggested descriptive scale:
- **0:** no identifiable catalyst found;
- **1–3:** plausible, sourced development;
- **4–7:** material development with independent corroboration;
- **8–10:** unusually strong, directly relevant and well-corroborated development.

Each catalyst record must retain event date, publication date, source reference, business relevance and confidence. The report must say **“visible catalyst”**, not “reason FIIs bought”, unless an institution has explicitly disclosed that rationale. Catalyst points are not added to the FII score and do not upgrade evidence grade.

## 4. Output contract

Each stock/horizon report should show:

- FII Accumulation Score and coverage;
- Market Confirmation Score and coverage;
- catalyst assessment and source-backed explanation;
- evidence grade and direct-transaction gate result;
- as-of date and source provenance;
- unavailable dimensions and plain-language inclusion reason;
- short-term (1–4 week) and positional (1–6 month) views separately.

Market-wide FII/DII cash flows and participant-category derivatives positioning are shown in a separate market-regime panel, never allocated to individual stocks.

## 5. Validation before production

Do not enable production rankings until the model has:
1. sufficient historical source depth and validated date semantics;
2. walk-forward testing with point-in-time data only (no look-ahead);
3. rank stability and sensitivity analysis for weights and missing-data coverage;
4. false-positive analysis against subsequent disclosed accumulation/ownership changes;
5. separate evaluation of short-term and positional horizons;
6. documented review of score distributions and rankability gates.

The proposed weights are transparent starting assumptions, not proven predictive coefficients. Calibrate them only after the historical dataset is reliable. No score is a buy recommendation or proof of undisclosed institutional intent.
