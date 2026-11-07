# Official NSE data source register

Research checked: 7 November 2026. This register distinguishes source availability from historical completeness. A report appearing on an exchange page does not imply an unrestricted bulk archive or a stable API.

## Primary source matrix

| Dataset | Primary source | What it can establish | Caveat / handling |
|---|---|---|---|
| Current Nifty 500 members | [NSE Indices Nifty 500 page](https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-500), [official constituent CSV](https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv) | Live-validated 7 Nov 2026: 501 rows with company, industry, symbol, series and ISIN; required fields populated and symbols/ISINs unique | Snapshot only. Preserve original bytes, normalized content, retrieval timestamp and SHA-256. The page says data is as on the last trading day of the previous month; do not infer a precise effective date or backfill historical membership from today's list. |
| Nifty 500 methodology | [Nifty Indices Nifty 500 page](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500) | Index selection and methodology context | Version methodology documents and record effective dates when rules change. |
| Daily FII/FPI and DII cash activity | [NSE FII/DII reports](https://www.nseindia.com/reports/fii-dii) | NSE-exclusive and combined NSE/BSE/MSEI market-level cash activity | NSE labels FII/FPI data provisional and subject to custodial confirmation and modifications. Preserve provisional/final state; never interpret market-wide net flow as buying in a named stock. |
| Final FPI cash data | [NSDL FPI reports](https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx) and [CDSL foreign portfolio investor data](https://www.cdslindia.com/Publications/ForeignPortInvestor.html) | Final FPI data where available | Separate finalization source and publication date from trade date. Check field definitions before reconciling with NSE provisional data. |
| Stock/index futures and options daily contracts | [NSE All Reports – Derivatives](https://www.nseindia.com/all-reports-derivatives), UDiFF Common Bhavcopy Final ZIP | Contract-level daily price, volume and open-interest fields, subject to schema validation | Use UDiFF final format; NSE marks older common bhavcopy formats discontinued from 8 July 2024. Retain contract identifiers and expiry/strike/option type. |
| Derivatives contract master | NSE All Reports – Derivatives, F&O MII contract file | Contract metadata needed to decode instruments and expiries | Version daily files; validate schema and effective date. |
| Participant-wise derivatives OI and volume | NSE All Reports – Derivatives, F&O Participant-wise Open Interest and Trading Volumes | Aggregate positioning/volume by participant category | These are participant-category totals, not participant-by-stock positions. They must never be allocated to individual equities or used as proof of FII buying in a named stock. |
| FII derivatives statistics | NSE All Reports – Derivatives, F&O FII derivatives statistics | Aggregate FII derivatives statistics as defined by the report | Keep separate from stock-level contract OI. Inspect workbook definitions before mapping metrics. |
| Stock MWPL / ban context | NSE All Reports – Derivatives, client position % of stock MWPL and security-in-ban-period reports | Market-wide position-limit context and ban status | Preserve the report date and applicable denominator; do not infer stock-level FII ownership from client/MWPL reports. |
| Equity daily OHLCV and delivery | NSE equity reports / official bhavcopy archives | Security-level cash-market price, volume and delivery fields where published | Validate current and legacy schemas separately; retain series and ISIN. |

## Required ingestion rules

1. **Immutable raw first:** store the original downloaded bytes before parsing. Never overwrite a source artifact.
2. **Provenance:** record publisher, report name, source URL, trade/report date, published timestamp when available, retrieval timestamp (UTC), provisional/final flag, byte size, SHA-256, parser version and schema version.
3. **No silent source substitution:** NSE-exclusive and combined-market FII/DII datasets are different series. Keep them separate.
4. **No false attribution:** participant-wise F&O reports are category aggregates. Stock-level futures/options OI is market-wide contract activity and does not reveal which participant category created it.
5. **Finality is explicit:** provisional NSE FII/FPI cash observations may be revised. Preserve revisions as new source versions rather than mutating history.
6. **Schema drift fails loudly:** unknown columns, missing required fields, duplicate contracts, invalid dates and unexpected instrument codes must fail validation and enter a quarantine path.
7. **Coverage is reported:** missing dates or unavailable historical files are marked unavailable, not zero activity.
8. **Point-in-time constituents:** store each official Nifty 500 snapshot and separately reconstruct membership intervals only from dated snapshots and documented change events. Label inferred boundaries as inferred.

## Initial implementation order

1. Fetch and validate the official current Nifty 500 CSV; persist raw bytes and a normalized snapshot.
2. Fetch daily NSE equity security master/reference data and reconcile symbol, series and ISIN.
3. Capture daily F&O contract master and UDiFF final bhavcopy; build a schema contract from actual files.
4. Capture participant-wise OI/volume and FII derivatives statistics as separate market-level tables.
5. Add NSE provisional and NSDL/CDSL final cash-flow ingestion with explicit dataset identity and finality.
6. Only after raw-file fixtures and schema tests exist, schedule automated daily ingestion.

## Source references

- NSE Nifty 500 index page: https://www.nseindia.com/static/products-services/indices-nifty500-index
- NSE FII/FPI & DII activity: https://www.nseindia.com/reports/fii-dii
- NSE All Reports – Derivatives: https://www.nseindia.com/all-reports-derivatives
- NSDL FPI reports: https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx
- CDSL foreign portfolio investor data: https://www.cdslindia.com/Publications/ForeignPortInvestor.html
- NSE Indices Nifty 500: https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500
