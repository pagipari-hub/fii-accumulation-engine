# FII Accumulation Score — evidence and calibration specification

## Purpose

The FII Accumulation Score is intended to measure verified, stock-specific FPI accumulation. It is not a market-sentiment score and is not an investment recommendation.

A positive market-wide FPI cash-flow number, participant-wise derivatives position, price rise, delivery spike, or volume spike does **not** establish that FPI investors bought a particular stock.

## Proposed score architecture

### 1. FII Accumulation Score — 0 to 100

| Dimension | Weight | Required evidence |
|---|---:|---|
| Verified net FPI transactions | 35 | Stock-specific official gross purchases and gross sales; net must be calculable |
| Persistence | 25 | Repeated verified stock-specific net FPI buying over dated observations |
| Quarterly FPI ownership change | 20 | At least two comparable, identity-verified quarterly shareholding snapshots |
| Independent FPI buyer breadth | 10 | Distinct FPI clients with registration valid on the transaction date |
| Relative accumulation intensity | 10 | Verified net accumulation relative to a documented turnover/free-float denominator |

The weights are design weights, not calibrated cut-offs. Thresholds and transformations remain unset until historical calibration is completed.

### Hard eligibility gate

The score is N/A unless all of the following hold:

1. The security is a tradable member of the eligible universe, not an index placeholder.
2. Security identity is exact and dated. ISIN is primary; any alternate crosswalk must match the dated source, filing record, XBRL URL and payload hash.
3. Stock-specific FPI transaction evidence is available from an official source, with gross purchases and gross sales both accounted for.
4. FPI client identity is temporally verified when named-client attribution is used.
5. The observation window is reproducible and historical coverage supports persistence.
6. The quarterly ownership series contains comparable, verified periods.

If a required component is missing, the overall FII Accumulation Score is N/A; missing data is never converted to zero. Market-wide flows cannot satisfy any stock-specific requirement.

### 2. Market Confirmation Score — 0 to 100

This is a separate technical confirmation measure and cannot compensate for missing FPI evidence.

| Dimension | Weight |
|---|---:|
| Delivery strength | 35 |
| Relative price strength | 30 |
| Volume confirmation | 20 |
| Trend stability | 15 |

This score also remains unpublished until historical calibration, reproducibility and stability checks pass.

### 3. Catalyst assessment — 0 to 10

A separate sourced assessment may record dated business, corporate, sector, earnings, policy or valuation developments. It describes visible context only. It must never be presented as the actual motive of an FPI buyer unless the source explicitly establishes that connection.

## Evidence and missingness rules

- N/A means the required evidence is unavailable or unverified; it is not a negative score.
- Gross purchases and gross sales must remain separately visible. Net alone is insufficient context.
- Partial named-client gross-buy evidence is a lead, not proof of net accumulation.
- A current FPI registry match does not establish that the registration was active on an earlier transaction date.
- Quarterly ownership is a snapshot, not the date of a purchase.
- Market-wide FPI/DII cash flows and derivatives statistics are market context only.
- Price, volume and delivery confirmation are not institutional identity evidence.
- No company-name-only identity override is permitted.
- A non-tradable corporate-action placeholder remains in the index snapshot but is excluded from tradable-stock scoring.

## Calibration and release gates

Before numeric scores or rankings are enabled:

1. Build reproducible stock-specific historical evidence with raw source hashes and exact ISIN identity.
2. Establish sufficient dated transaction and comparable quarterly ownership coverage.
3. Define transformations and thresholds using historical data only; prevent look-ahead.
4. Run walk-forward and out-of-sample calibration.
5. Measure false-positive behaviour, stability and missing-data sensitivity.
6. Retain score-component explanations and the source evidence supporting each component.
7. Keep the production score disabled until all gates are independently validated.

## Current evidence status (2026-11-11)

- The 501-row index snapshot contains 500 tradable securities plus the DUMMYHEG demerger placeholder.
- The dated BSE identity crosswalk verifies 146 of 146 identity candidates.
- Quarterly ownership is parsed and identity-verified for 494 of 500 tradable securities (98.8%); six remain unavailable because the NSE master has no current filing and the BSE corporate shareholding endpoint returned HTTP 403 from the collection runner.
- The NSDL archive provides 62 overlapping market-wide FPI/DII cash-equity observations. These are not attributed to stocks.
- The current deal evidence contains one Grade 2 named FPI-client gross-buy lead. Registration timing is unverified and known net buy/sell totals are incomplete.

**Production FII Accumulation Score: disabled / N/A.** The current evidence is not sufficient to claim verified stock-specific net FPI accumulation or to calibrate a trustworthy ranking.
