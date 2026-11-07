# NSE quarterly equity shareholding-pattern source

## Official source

- Page: https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern/
- Equity table schema: https://www.nseindia.com/json/CorporateFiling/shareholder/shareholder-equity.json
- Equity master: https://www.nseindia.com/api/corporate-share-holdings-master?index=equities
- RSS: https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml

The endpoint paths above are taken from the official NSE page's same-origin JavaScript and HTML. The page separates Equity and SME shareholding patterns and provides company and date filters, CSV download and filing detail.

## Probe

`src/fii_engine/nse_shareholding_probe.py` captures the official schema JSON, equity master response and RSS XML after bootstrapping a session from the report page. It stores raw bytes, HTTP metadata, hashes, top-level keys, columns/row samples and RSS item details as a workflow artifact. It is a research probe, not a production ingestion adapter.

## What must be established before production

1. The master response's exact row schema and whether it is a full historical master or a limited current view.
2. Distinct fields for quarter-end date, filing/submission date, last modified/revised date and any record identifier.
3. How CSV and XBRL links are represented, and whether the same filing can be revised under a new record ID.
4. How explicit FPI category rows are encoded (including FPI Category I/II or other applicable subcategories) and how share counts/percentages reconcile to totals.
5. How to identify the latest valid filing for a company-quarter without discarding prior revisions.

Quarter-end date and filing date must never be conflated. An increase in FPI ownership is evidence of reported ownership change, but not proof of daily accumulation or the date the shares were purchased. The RSS feed is useful for change detection but is not assumed to be complete historical coverage.
