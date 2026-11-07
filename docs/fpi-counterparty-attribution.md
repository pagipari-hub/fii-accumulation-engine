# FPI counterparty attribution from NSE bulk/block deals

## Purpose

NSE bulk and block deal disclosures contain named client counterparties. This pipeline cross-checks those names against the official CDSL registered-FPI directory and creates an auditable stock-level transaction view.

This is direct evidence about disclosed large transactions. It is not a complete record of all FPI trading: ordinary exchange-market trades do not disclose named counterparties in this way.

## Identity rules

- Normalize case, punctuation/spacing and explicitly supported legal suffix variants.
- Require exact normalized-name matches. No fuzzy, substring or symbol-based identity inference.
- Preserve the source client name, matched registry name, registration number, validity end date and source hashes.
- If a name maps to multiple registration numbers, mark the match ambiguous; do not select one arbitrarily.
- A name absent from the current CDSL register is unresolved, not proof that the client is not an FPI.
- Treat a registered entity whose name includes ODI as an ODI issuer. Do not count it as a disclosed beneficial FPI investor.

## Time and transaction semantics

The CDSL register exposes validity end dates but not registration start dates. A current registry snapshot collected after a historical deal date cannot prove the entity was registered on that earlier date. The output therefore records:
- observed_in_registry_on_trade_date when the register snapshot date equals the deal date and validity supports the date;
- post_trade_snapshot_registration_start_unknown when the register snapshot is later;
- separate expired, pre-trade and unknown-validity states.

Bulk and block disclosures can contain the same underlying transaction. The classifier pairs identical transaction rows appearing in both report types using trade date, symbol, security, normalized surface client name, side, quantity and price. It records both source reports but counts the paired transaction once. Repeated rows within one report type are retained.

## Outputs

- fpi_deal_counterparty_current.csv: one deduplicated disclosed event with identity status, registration metadata, temporal status, source report types, source files and fingerprints.
- fpi_deal_stock_summary_current.csv: stock-level matched FPI client buys/sells, ODI issuer activity, unmatched quantities, cross-report duplicate counts, identity coverage and temporal limitations.
- Immutable snapshots and a manifest preserve source paths and hashes.

current_registry_matched_fpi_client_known_net_quantity is only the net of currently registry-matched direct FPI client events. It is not a complete FPI net flow when unmatched counterparties or ODI issuers are present. Even a fully matched set remains temporally qualified by the registry snapshot date.

## Evidence use

A named bulk/block transaction matched to a current CDSL FPI entry is a strong stock-specific disclosed-counterparty signal. It does not alone prove sustained accumulation, beneficial ownership, or a multi-month position. Confirm persistence with subsequent disclosures, quarterly shareholding changes and independent cash/price/delivery/derivatives evidence.


## Nifty 500 universe boundary

Every disclosed deal event is joined to the current official Nifty 500 constituent file by exact ISIN. A matching symbol with a missing ISIN is not promoted to verified membership; a symbol/ISIN conflict is explicitly flagged. Events outside the current Nifty 500 remain in the audit output but are not eligible for the Nifty 500 watchlist. The source page states its constituent data is as of the last trading day of the previous month, so the snapshot's stated freshness must be respected rather than inventing a more precise effective date.
