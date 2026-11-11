# Current evidence-matrix scoring adapter (v0.2 research)

## Purpose

`fii_engine.matrix_scoring` adapts the current 501-stock evidence matrix to the v0.2 scoring contract. It does **not** enable a production FII watchlist.

Run:

```bash
python -m fii_engine.matrix_scoring \
  --matrix data/raw/fpi_evidence_blend/fpi_evidence_matrix_current.csv \
  --matrix-ref "data/raw/fpi_evidence_blend/fpi_evidence_matrix_current.csv#sha256=<matrix-sha256>" \
  --output data/raw/fpi_evidence_blend/fpi_scoring_current.json
```

The research workflow runs the full test suite, builds the dated JSON output, checks the full-universe and evidence gates, and uploads the result as a GitHub Actions artifact.

## What can be scored today

The adapter computes a **Market Confirmation Score** using cross-sectional percentiles across the same dated Nifty 500 universe:

- Delivery strength: percentile of the change between 5-session average delivery percentage and the 20-session baseline.
- Relative price strength: mean cross-sectional percentile of available 5/20/60-session returns; at least two complete horizons are required.
- Volume confirmation: percentile of 5-session versus 20-session average daily volume ratio.
- Trend stability: a directional-breadth proxy using positive 5/20/60-session returns. It is explicitly not a realized-volatility or drawdown statistic.

All input features must carry complete source status. Missing or partial windows remain unavailable. Percentile ranks use mid-ranks for ties. This is a research proxy, not a calibrated predictor; the feature definitions and their correlations require review and historical validation.

## Why the FII Accumulation Score remains N/A

The current matrix has 501 stocks, but its current point-in-time state does not supply the inputs required for a verified stock-level FII score:

- Temporally verified FPI buy and sell quantities are zero across the current matrix; the four current-registry leads have registration start dates unverified for the transaction dates.
- Only a current disclosed-deal snapshot is available, not repeated weekly net FPI observations.
- The matrix has one current quarterly ownership snapshot, not a comparable prior identity-verified filing for change calculation.
- Consolidated beneficial-owner breadth is not represented as a validated per-stock weekly net buyer observation.
- Temporally verified net buy value is unavailable for liquidity-normalized accumulation intensity.

Therefore the adapter records all five FII dimensions as unavailable, rather than zero. The FII score is null and no stock is FII-rankable from this data snapshot. This is an honest data-coverage result, not a failure of the scoring engine.

## Catalysts

No dated catalyst feed is connected to the matrix adapter. Catalyst status is therefore `unavailable` with a null score. It must not be represented as "no catalyst found" or as a zero. When a complete catalyst-research process is later connected, a verified search with no material event may be represented as an observed zero with provenance.

## Output interpretation

Every row carries:
- separate FII and market-confirmation scores and coverage;
- rankability/status for each;
- catalyst status, score and summary;
- matrix source reference, as-of date, ISIN and original FPI evidence grade;
- direct-deal temporal status.

The current adapter is intended to expose the gap between the market-behaviour evidence already available and the stock-specific FPI evidence still missing. It does not convert market-wide FII/DII flows or participant derivatives aggregates into stock-level FII signals.

## Production gate

Do not use this output as a buy list. Production ranking requires validated historical FPI event coverage, repeated weekly observations, comparable ownership snapshots, point-in-time identity resolution, catalyst provenance, walk-forward tests, rank stability and false-positive analysis.
