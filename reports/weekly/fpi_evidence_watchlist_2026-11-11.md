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
- **Tradable securities without parsed equity shareholding or separate REIT unitholding evidence:** 3.

Remaining uncovered EQ shareholding records: ABBOTINDIA, ALKEM, MCX.
These remain N/A; no ownership values are imputed.

## REIT unitholding FPI evidence (separate ownership data type)

These official REIT unit-holding disclosures are not mixed with equity shareholding XBRL or treated as equity shares.

| REIT | Reporting date | FPI units | FPI ownership | Source attachment SHA-256 |
|---|---|---:|---:|---|
| BAGMANE — Bagmane Prime Office REIT | 2026-09-30 | 7,486,373 | 0.22% | f8cedb88375d56ad7a50453cbfd438cce150d8408d6da5cab98e30e5b81f9a4c |
| BIRET — Brookfield India Real Estate Trust | 2026-09-30 | 88,393,039 | 10.65% | 667ae75caf0d68d0c3e703fa648cea693ce8e7f4783efe553ae39a2bce9fd8e1 |
| EMBASSY — EMBASSY OFFICE PARKS REIT | 2026-09-30 | 302,751,052 | 31.94% | bf299766b4a206da6ba32ac654a42dbb30707a265b9244e8240e050bc44950b0 |

Parsed official REIT disclosures: 3 / 3. These are quarter-end ownership snapshots, not dated purchases or proof of FPI accumulation.

## Disclosed stock-specific deal evidence window

- **NSE bulk/block deal disclosure window:** 2026-11-02 to 2026-11-09 (7-day span).
- **Source records / deduplicated events:** 661 / 650.
- **Current-registry-matched FPI-client events / unmatched events:** 10 / 638.
- **CDSL FPI registry snapshot date:** 2026-11-11.
These are disclosed bulk/block transactions, not a complete tape of all FPI trades. Current-registry matches do not prove registration was active on earlier trade dates.

## Stock-specific FPI evidence

- **Grade 1 candidates:** 0
- **Grade 2 candidates:** 1
- **Named FPI-client gross-buy candidates:** 1
- **Other tradable securities without a qualifying named-client gross-buy candidate:** 499.

| Security | Evidence grade | Deal evidence pattern | Registry timing | Known net status |
|---|---:|---|---|---|
| JSWCEMENT — JSW Cement Ltd. | grade_2_named_client_current_registry_match_temporal_status_unverified | matched_fpi_client_known_net_buy_partial | post_trade_snapshot_registration_start_unknown | partial_unmatched_or_odi_counterparties |

### JSW Cement Ltd. (JSWCEMENT)

- **Current-registry-matched FPI client gross buys / sells:** 12,000,000 / 0 shares.
- **Unmatched gross sells:** 19,098,281 shares; counterparty identity coverage 50.00%.
- **Registry timing:** post trade snapshot registration start unknown.
- **Known FPI net status:** partial unmatched or odi counterparties.
- **Short term (5 sessions):** price return -0.51%; average delivery 50.64%.
- **Positional (20 / 60 sessions):** price returns +8.35% / -7.42%; delivery context 5-session average delivery is at or above the 20-session average.
- **Quarterly ownership:** FPI total 4.59% as of 2026-09-30; Quarterly FPI ownership baseline available; no prior-quarter change series.
- **Derivatives:** futures Not applicable; options Not applicable; Options PCR has partial observations.
- **Why included:** A named current-registry FPI-client gross-buy disclosure is visible, but registration timing is not verified for the transaction date and sell-side counterparty identity is incomplete. This is a lead, not confirmed net accumulation.
- **Catalyst:** No sourced business/corporate catalyst or buyer motive is attached; none is inferred.

A Grade 2 gross-buy match is a lead, not proof of net FPI accumulation. Unverified registration timing and incomplete buy/sell totals must remain explicit.

## Aggregate market-wide cash-flow context

- **NSDL observation window:** 2026-08-10 to 2026-11-09 (62 dates).
- **FPI cash-equity gross purchases / sales:** ₹1,081,290.98 / ₹1,081,546.19 crore.
- **FPI cash-equity net:** ₹-255.21 crore across 62 observations; 28 positive and 34 negative days.
- **DII cash-equity gross purchases / sales:** ₹1,219,903.88 / ₹988,605.12 crore.
- **DII cash-equity net:** ₹231,298.85 crore across 62 observations; 54 positive and 8 negative days.
- **Overlapping FPI/DII dates:** 62.
- **Explicit DII No Data dates:** 2026-08-26, 2026-09-14, 2026-10-02, 2026-10-20, 2026-11-10.
- **Source construction:** NSDL FPI Equity Sub-total and explicit DII Equity Total; debt, derivatives and duplicate investor subtotals are excluded.
These are aggregate market-wide flows only. They do not establish which individual stocks were bought or sold.

## Historical ownership source discovery

- **NSE market-wide shareholding master:** 4,970 rows across 2,402 symbols (01-07-2026 to 11-11-2026).
- **NSE symbol-specific master history rows:** {"ABBOTINDIA": {"date_range": 0, "symbol_history": 0}, "ALKEM": {"date_range": 1, "symbol_history": 20}, "HDFCBANK": {"date_range": 2, "symbol_history": 23}, "MCX": {"date_range": 0, "symbol_history": 0}, "RELIANCE": {"date_range": 2, "symbol_history": 23}}.
- **Symbols returning multiple master records:** RELIANCE, HDFCBANK, ALKEM.
- **NSE shareholding/unitholding pattern announcements:** {"ABBOTINDIA": 0, "ALKEM": 0, "BAGMANE": 3, "BIRET": 5, "EMBASSY": 4, "MCX": 0}.
- **Official attachments captured / failed:** 12 / 0.
These probes discover historical filing sources only. Their records are not used as ownership values until quarter dates, category totals, security identity and payload hashes are validated.

## BSE fallback coverage

- **BSE corporate shareholding API access:** http_403_blocked.
- **Probe statuses:** {"not_attempted_after_exchange_403": 2, "probe_error": 1}.
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
- BSE fallback manifest SHA-256: 544528858ab7c0b4b26ef62fad3113c7d60e05099ca4e3f421ad288115c23c74
- Ownership identity crosswalk SHA-256: 12258998e48b1203bbcbc708d6d3ee9c5fcb9f7d537d925d47ae3c392a5d52c8
- NSDL normalized CSV SHA-256: 061bc2be35b00e26bd6250938c005ba1998fbc01044cac72c2d0e7cdf216d4c1
- NSDL raw archive manifest SHA-256: b1f08e3af954dba8db2182464433b111311c74a2e29240893c8c5d25dc42fb3a
- The accompanying run artifact retains raw source responses, manifests and normalized inputs.
This is an evidence watchlist, not an investment recommendation.
