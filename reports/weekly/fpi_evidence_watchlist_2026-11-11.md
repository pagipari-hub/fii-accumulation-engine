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
- **Grade 2 candidates:** 1
- **Named FPI-client gross-buy candidates:** 1

| Security | Evidence grade | Deal evidence pattern | Registry timing | Known net status |
|---|---:|---|---|---|
| JSWCEMENT — JSW Cement Ltd. | grade_2_named_client_current_registry_match_temporal_status_unverified | matched_fpi_client_known_net_buy_partial | post_trade_snapshot_registration_start_unknown | partial_unmatched_or_odi_counterparties |

A Grade 2 gross-buy match is a lead, not proof of net FPI accumulation. Unverified registration timing and incomplete buy/sell totals must remain explicit.

## Aggregate market-wide cash-flow context

- **NSDL observation window:** 2026-08-10 to 2026-11-09 (62 dates).
- **FPI cash-equity gross purchases / sales:** ₹1,081,290.98 / ₹1,081,546.19 crore.
- **FPI cash-equity net:** ₹-255.21 crore across 62 observations; 28 positive and 34 negative days.
- **DII cash-equity gross purchases / sales:** ₹1,219,903.88 / ₹988,605.12 crore.
- **DII cash-equity net:** ₹231,298.85 crore across 62 observations; 54 positive and 8 negative days.
- **Overlapping FPI/DII dates:** 62.
- **Explicit DII No Data dates:** 2026-08-26, 2026-09-14, 2026-10-02, 2026-10-20, 2026-11-10.
These are aggregate market-wide flows only. They do not establish which individual stocks were bought or sold.

## BSE fallback coverage

- **BSE corporate shareholding API access:** http_403_blocked.
- **Probe statuses:** {"not_attempted_after_exchange_403": 5, "probe_error": 1}.
- **BSE UDiFF source SHA-256:** e2b7cea2672aed05df3ce55f3c531a153cb2b5215e2b493b2735cd9be116acce.
An HTTP 403 is an access restriction, not proof that a filing does not exist. No third-party ownership percentages were substituted.

## Scoring decision

- **FII Accumulation Score:** N/A — stock-level verified net FPI transactions and a calibrated historical ownership-change series are not yet available for the full universe.
- **Market Confirmation Score:** not published until its historical calibration and reproducibility gates pass.
- **Composite score/ranking:** disabled.
- Scoring weights, hard gates and calibration requirements are documented in docs/fii-accumulation-score-spec.md.
- Market-wide FPI/DII flows are context only and are never treated as evidence that a specific stock was bought.

## Reproducibility

- Matrix CSV SHA-256: d96df24dc28ccb0feff1fdddfe565154b0defdc7d494f85369b66f49e845d6dd
- BSE fallback manifest SHA-256: 88f67dfaf90b4106fed2bb4b0fda1edf41257b87bf9fa2e4dbc7d95dad02cd92
- Ownership identity crosswalk SHA-256: 12258998e48b1203bbcbc708d6d3ee9c5fcb9f7d537d925d47ae3c392a5d52c8
- NSDL normalized CSV SHA-256: 061bc2be35b00e26bd6250938c005ba1998fbc01044cac72c2d0e7cdf216d4c1
- NSDL raw archive manifest SHA-256: b1f08e3af954dba8db2182464433b111311c74a2e29240893c8c5d25dc42fb3a
- The accompanying run artifact retains raw source responses, manifests and normalized inputs.
This is an evidence watchlist, not an investment recommendation.
