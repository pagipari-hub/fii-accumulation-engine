# CDSL FPI source research

## Official pages identified

- Daily FPI investment and derivatives: https://www.cdslindia.com/eservices/publications/fiidaily
- Current-month daily FPI investment and derivatives: https://www.cdslindia.com/eservices/publications/FIIMonthly
- Historical FPI investment archive landing page: https://www.cdslindia.com/Publications/FIITrends.aspx
- Official archive popup data page discovered in the landing page's own JavaScript: https://www.cdslindia.com/Publications/ArchiveDataAfter2009.aspx
- Trade-wise equity FPI data: https://www.cdslindia.com/Publications/EquityDataFII.html
- Registered FPI directory, alphabetic pages: https://www.cdslindia.com/Publications/RegForeignInstInvstr.aspx?indexname=A (replace the index letter with B-Z)

## What the public pages establish

- The daily/current-month pages expose custodian-reported FPI investment activity, including gross purchases, gross sales and net investment, with investment route and asset class separated.
- The same daily/current-month pages expose FPI derivative activity by product family, including buy/sell contracts and amounts plus end-of-day open interest.
- CDSL describes the daily investment figures as based on reports submitted by custodians and covering activity on and up to the previous trading day(s).
- The archive landing page opens an official popup data page, `ArchiveDataAfter2009.aspx`, which exposes daily current-month FPI investment and derivative tables plus annual/cumulative totals. The date-picker POST smoke test returned HTTP 200 but did not return the requested date; do not treat it as a historical-date API until the form flow is understood.
- The trade-wise equity page documents transaction-level fields including custodian code, report date, transaction ID, masked FII/sub-account/broker registration numbers, scrip name, ISIN, transaction date/type, exchange, settlement, rate, quantity, value and reporting/amendment metadata.
- CDSL documents transaction codes that distinguish secondary-market purchases and sales from primary-market, corporate-action and other non-trading events.

## Why this matters to the engine

CDSL is a potential independent, custodian-reported FPI source to complement NSE provisional daily FII/FPI and DII flows. It can improve the market-wide FPI context and may provide transaction-level stock attribution where the trade-wise archive is available.

## Controls before production ingestion

- Do not assume the CDSL daily page is final for the same trade date as NSE provisional flows; retain reporting date, publication date, and custodian-reported coverage separately.
- Do not combine equity stock-exchange trades with primary-market/other events, debt, hybrid, mutual funds or AIFs.
- Do not treat derivative amounts as cash-equity purchases or stock-specific positions.
- Trade-wise FII registration and sub-account identifiers are masked in the documented format. Custodian code and transaction ID are visible, but the beneficial investor identity is not. Do not claim named-FII attribution.
- Preserve amendment and cancellation codes; transaction-level gross records must be deduplicated/reconciled before computing net purchases by ISIN.
- Match listed equities by exact ISIN first. A present but mismatched ISIN must not be overridden by symbol or company-name matching.
- Verify archive coverage and file schemas from captured official responses before backfilling or scoring.
- Keep raw response hashes, retrieval timestamps, page URLs, reporting dates and parser versions.

## Current status

The source probe is discovery-only. It captures official page HTML, response metadata, table samples and archive links. No CDSL figures enter scoring until archive coverage, table structure, amendments, transaction semantics and identity joins are validated against real downloadable records.


## Production registry snapshots

The production collector fetches all 26 alphabetic CDSL directory pages, preserves raw HTML in a 90-day workflow artifact, and writes immutable normalized CSV snapshots with per-page SHA-256 provenance. Duplicate registration numbers and duplicate names are retained and counted; they are not silently collapsed. Name normalization is deterministic and conservative (case, punctuation/spacing, and explicit legal suffix variants only); no fuzzy matching is used.

The current directory snapshot exposes validity end dates but not registration start dates. Therefore:
- A counterparty found in the current register is a current-snapshot identity match.
- A same-day registry snapshot can support presence on the trade date, subject to the published validity end date.
- A registry snapshot collected after a historical trade date cannot, by itself, prove that the entity was registered on that earlier date.
- A name absent from the register is unresolved, not proof that the counterparty is not an FPI.


The source-discovery probe now focuses on CDSL daily/monthly flow, archive and trade-wise source pages. The 26 alphabetic registered-FPI pages are fetched only by the dedicated production registry collector, avoiding duplicate requests and separating discovery from maintained identity data.
