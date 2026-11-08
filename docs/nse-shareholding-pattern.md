# NSE quarterly equity shareholding-pattern source

## Official source

- Page: https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern/
- Equity table schema: https://www.nseindia.com/json/CorporateFiling/shareholder/shareholder-equity.json
- Equity master: https://www.nseindia.com/api/corporate-share-holdings-master?index=equities
- RSS: https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml

The endpoint paths above are taken from the official NSE page's same-origin JavaScript and HTML. The page separates Equity and SME shareholding patterns and provides company and date filters, CSV download and filing detail.

## Probe findings

`src/fii_engine/nse_shareholding_probe.py` established the official schema, master and RSS endpoints. The current master response contained 2,370 records for the 30-Sep-2026 reporting quarter, including revised filings and XBRL links. The RSS feed is a change-discovery aid, not a complete historical source.

The XBRL parser reads explicit `CategoryOfShareholdersAxis` members. FPI Category I and II remain separate from other foreign institutions. It validates instant reporting periods, preserves entity identifiers and dimensions, and rejects ambiguous duplicate contexts rather than silently overwriting them.

## Production ingestion

`src/fii_engine/nse_shareholding_ingest.py` now:

- Captures the official equity master after an NSE session bootstrap.
- Joins to the current Nifty 500 by exact ISIN. If the master ISIN is missing, symbol plus exact normalized company name is required. A present but mismatched ISIN is rejected.
- Fetches all eligible filing records, including revisions, and preserves them append-only by record ID and metadata hash.
- Stores normalized filing JSON, master snapshots and quarterly ownership CSV snapshots in the repository. Raw XBRL XML and raw master payloads remain in workflow artifacts.
- Selects the latest filing for each company-quarter by broadcast/system timestamp while retaining earlier revisions.
- Extracts FPI Category I/II shares and percentage points, while keeping other foreign institutions and explicitly reported overseas-depository holdings separate.
- Reconciles the foreign-institutions aggregate using overseas-depository holdings only when that category is explicitly present. An absent or nil component is not assumed to be zero.
- Parser-versioned filing snapshots allow a parser correction to be re-applied to existing XBRL records without overwriting immutable earlier parses.

The master API may expose only the currently available filing period. Captured quarterly snapshots accumulate from the first production run; no earlier quarter is invented or assumed. Until two validated quarters exist for a stock, quarter-on-quarter ownership change is unavailable.

Quarter-end date and filing date must never be conflated. An increase in FPI ownership is evidence of reported ownership change, but not proof of daily accumulation or the date the shares were purchased. This dataset is ownership evidence, not a daily transaction feed.
