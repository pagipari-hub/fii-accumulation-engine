# Weekly FPI evidence watchlist

- **Market data as of:** 2026-11-09
- **Index rows:** 501
- **Tradable securities:** 500
- **Non-tradable index placeholders:** 1

## Coverage and data quality

- **Quarterly ownership parsed and identity-verified:** 494 / 500 (98.8%).
- **Coverage across all index rows:** 98.6%.
- **Parsed verified ownership rows:** 494.
- **Dated BSE identity crosswalk:** 146 / 146 verified; 0 unresolved.
- **BSE UDiFF source SHA-256:** e2b7cea2672aed05df3ce55f3c531a153cb2b5215e2b493b2735cd9be116acce.
- **Ownership rows not found in the current NSE filing master:** 6.

Missing current NSE ownership records: ABBOTINDIA, ALKEM, BAGMANE, BIRET, EMBASSY, MCX.
These remain N/A; no ownership values are imputed.

## Stock-specific FPI evidence

- **Grade 1 candidates:** 0
- **Grade 2 candidates:** 0
- **Named FPI-client gross-buy candidates:** 1

| Security | Evidence grade | Deal evidence pattern | Registry timing | Known net status |
|---|---:|---|---|---|
| JSWCEMENT — JSW Cement Ltd. | grade_2_named_client_current_registry_match_temporal_status_unverified | matched_fpi_client_known_net_buy_partial | post_trade_snapshot_registration_start_unknown | partial_unmatched_or_odi_counterparties |

A Grade 2 gross-buy match is a lead, not proof of net FPI accumulation. Unverified registration timing and incomplete buy/sell totals must remain explicit.

## Aggregate market-wide cash-flow context

NSDL aggregate cash-flow history has not been integrated into this matrix build.

## BSE fallback coverage

- **BSE corporate shareholding API access:** http_403_blocked.
- **Probe statuses:** {"not_attempted_after_exchange_403": 5, "probe_error": 1}.
- **BSE UDiFF source SHA-256:** e2b7cea2672aed05df3ce55f3c531a153cb2b5215e2b493b2735cd9be116acce.
An HTTP 403 is an access restriction, not proof that a filing does not exist. No third-party ownership percentages were substituted.

## Scoring decision

- **FII Accumulation Score:** N/A — stock-level verified net FPI transactions and a calibrated historical ownership-change series are not yet available for the full universe.
- **Market Confirmation Score:** not published until its historical calibration and reproducibility gates pass.
- **Composite score/ranking:** disabled.
- Market-wide FPI/DII flows are context only and are never treated as evidence that a specific stock was bought.

## Reproducibility

- Matrix CSV SHA-256: d96df24dc28ccb0feff1fdddfe565154b0defdc7d494f85369b66f49e845d6dd
- BSE fallback manifest SHA-256: f122556275149a203d84672b5afe71b85c7a8966de9643b1df4bd808130212f0
- Ownership identity crosswalk SHA-256: e2b7cea2672aed05df3ce55f3c531a153cb2b5215e2b493b2735cd9be116acce
- The accompanying run artifact retains raw source responses, manifests and normalized inputs.

This is an evidence watchlist, not an investment recommendation.
