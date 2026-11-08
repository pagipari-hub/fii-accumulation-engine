# Blended FPI Accumulation Evidence Matrix

## Purpose

This dataset combines independent stock-specific evidence across the full latest published Nifty 500 universe. It is an evidence matrix, not a ranking or trading signal. It deliberately does not calculate a composite score until the underlying history and attribution coverage are sufficient.

Current output:

- data/raw/fpi_evidence_blend/fpi_evidence_matrix_current.csv
- data/raw/fpi_evidence_blend/fpi_evidence_matrix_current_manifest.json

Immutable snapshots are stored under data/snapshots/fpi_evidence_blend/.

## Evidence families

1. **Named FPI-client bulk/block disclosures** — NSE disclosed client names are matched to the official CDSL registered-FPI directory using exact normalized names. ODI issuers, ambiguous registrations and unmatched counterparties remain separate. The NSE deal export does not supply ISIN; Nifty 500 membership therefore requires exact symbol plus exact normalized company name. The universe ISIN is enriched from the official constituent file.
2. **Quarterly FPI ownership baseline** — NSE XBRL FPI Category I and II ownership, retained separately from other foreign institutions. Identity mismatches and unparsed filings remain null. Current coverage is partial and only one reporting quarter is available; no quarter-on-quarter change is inferred.
3. **Stock cash, price and delivery context** — daily observation plus 5-, 20- and 60-session return, average volume and delivery measures.
4. **Stock derivatives context** — futures buildup counts and same-expiry options PCR with family-specific availability statuses. Futures buildup is market-wide contract activity, not proof of FII positions. Options PCR rolling values are withheld when expiry comparability fails.
5. **Market-wide context** — NSE FII/FPI and DII cash flows, plus participant-category derivatives aggregates. These are market/category context only, never stock-specific attribution.

## Status semantics

- **parsed_verified_identity**: the quarterly filing identity is verified by the permitted join and the filing was parsed.
- **isin_mismatch / identity_unverified**: ownership values are not promoted into the matrix.
- **observed_disclosed_deals**: one or more disclosed deal events passed the Nifty 500 symbol/company-name join.
- **deal_records_present_but_universe_join_unverified**: deal rows exist for the symbol, but the event-level universe join did not pass.
- **no_disclosed_deal_in_available_snapshots**: no named bulk/block transaction appeared in the collected reports for this window. This does not mean zero FPI trading.
- **direct_fpi_client_gross_buy_candidate**: at least one buy event was matched to a currently listed CDSL FPI client. It is not a net-flow conclusion.
- **fpi_deal_evidence_grade**: Grade 1 requires same-day registry evidence; Grade 2 means a named client matches the current register but trade-date registration remains unverified. No grade is assigned without direct matched FPI-client buying.

## Temporal and attribution boundaries

- The current deal window and registry snapshot dates are recorded in the manifest. The CDSL directory does not publish registration start dates; a later registry snapshot cannot establish registration on an earlier trade date.
- The Nifty 500 source says constituent data is as of the last trading day of the previous month and does not supply an exact effective date in the feed. The matrix describes membership in the latest published snapshot, not guaranteed membership on every later deal date.
- Quarterly shareholding is an ownership snapshot, not a dated transaction or purchase date.
- Unmatched deal counterparties are unresolved, not presumed non-FPI. ODI issuers are not treated as beneficial FPI investors.
- A matched-client net is incomplete when unmatched, ambiguous or ODI counterparties remain.
- Participant OI/volume reports are category aggregates, not participant-by-stock positions.
- Market-wide FII/FPI flows cannot establish that a particular stock was bought by FIIs.

## Current data maturity

The latest validated inputs provide 60 stock sessions for price/delivery/derivatives features, one session for market-wide FII/FPI and DII flows, and one session for participant derivatives aggregates. Quarterly FPI ownership is available for 348 of 501 stocks (69.46%) for the 30 September 2026 reporting quarter. These limitations are recorded in every matrix manifest.

The output intentionally keeps independent fields and evidence patterns visible. No composite score or automatic buy ranking is issued.
