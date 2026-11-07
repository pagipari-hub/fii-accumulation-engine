# Official NSE data source register

Research checked: 7 November 2026. This register distinguishes source availability from historical completeness. A report appearing on an exchange page does not imply an unrestricted bulk archive or a stable API.

## Primary source matrix

| Dataset | Primary source | What it can establish | Caveat / handling |
|---|---|---|---|
| Current Nifty 500 members | [NSE Indices Nifty 500 page](https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-500), [official constituent CSV](https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv) | Live-validated 7 Nov 2026: 501 rows with company, industry, symbol, series and ISIN; required fields populated and symbols/ISINs unique | Snapshot only. Preserve original bytes, normalized content, retrieval timestamp and SHA-256. The page says data is as on the last trading day of the previous month; do not infer a precise effective date or backfill historical membership from today's list. |
| Nifty 500 methodology | [Nifty Indices Nifty 500 page](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500) | Index selection and methodology context | Version methodology documents and record effective dates when rules change. |
| NSE-only FII/FPI and DII cash activity | [NSE FII/DII page](https://www.nseindia.com/reports/fii-dii), `/api/fiidiiTradeReact` | Live-validated 6 Nov 2026; daily per-date snapshots, ₹ crore, two categories | FII/FPI is provisional; DII is exchange-reported. Market-wide only; no stock attribution. The API's first capture returned one date, so scheduled collection is required to build history. |
| Combined NSE/BSE/MSEI FII/FPI and DII cash activity | [NSE FII/DII reports](https://www.nseindia.com/reports/fii-dii), combined-market CSV | Separate combined market-level cash activity | Registered but schema/publishing semantics not yet live-validated. Do not mix with NSE-only series. |
| Final FPI cash data | [NSDL FPI reports](https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx) and [CDSL foreign portfolio investor data](https://www.cdslindia.com/Publications/ForeignPortInvestor.html) | Final FPI data where available | Separate finalization source and publication date from trade date. Check field definitions before reconciling with NSE provisional data. |
| Stock/index futures and options daily contracts | [NSE All Reports – Derivatives](https://www.nseindia.com/all-reports-derivatives), UDiFF Common Bhavcopy Final ZIP | Live-validated 7 Nov 2026 for trade date 6 Nov: 40,471 rows, 34 source columns, 218 stock symbols and four recognized instrument types | Use UDiFF final format; NSE marks older common bhavcopy formats discontinued from 8 July 2024. Retain contract identifiers and expiry/strike/option type. Volume/turnover units still require explicit semantic validation before scoring. |
| Derivatives contract master | NSE All Reports – Derivatives, F&O MII contract file | Contract metadata needed to decode instruments and expiries | Version daily files; validate schema and effective date. |
| Participant-wise derivatives OI and volume | NSE All Reports – Derivatives, `FO-PARTICIPANTWISE-OI` and `FO-PARTICIPANTWISE-TRADING-VOL` | Live-validated 6 Nov 2026: 48 normalized rows per report, by Client/DII/FII/Pro and index/stock futures/options | Participant-category aggregates, not participant-by-stock positions. OI has observed one-contract source reconciliation differences; volume reconciles exactly. Never allocate these values to individual equities. |
| FII derivatives statistics | NSE All Reports – Derivatives, `FO-FII-DERIVATIVE-STAT` | Live-validated 6 Nov 2026: 16 normalized rows, including index-underlying detail and aggregate stock groups | FII aggregate only; stock futures/options are all-stock totals, not individual stock positions. Amount definitions and OI basis are documented; keep section totals distinct from details. |
| Stock MWPL / ban context | NSE All Reports – Derivatives, client position % of stock MWPL and security-in-ban-period reports | Market-wide position-limit context and ban status | Preserve the report date and applicable denominator; do not infer stock-level FII ownership from client/MWPL reports. |
| Cash-market OHLCV | [NSE All Reports – Equities](https://www.nseindia.com/all-reports), CM UDiFF Common Bhavcopy Final ZIP selected from the official CM catalogue | Adapter implemented; live validation pending. Final cash-market price, turnover, volume, ISIN, symbol and series | Current UDiFF format only; older common bhavcopy formats are discontinued. Preserve all instrument types/series and use ISIN+symbol+series for reconciliation. |
| Security-wise delivery | [NSE All Reports – Equities](https://www.nseindia.com/all-reports), Full Bhavcopy and Security Deliverable data | Adapter implemented; live validation pending. Traded quantity, deliverable quantity and delivery percentage by symbol/series | Delivery quantity is not participant attribution or beneficial ownership. Validate `DELIV_QTY <= TTL_TRD_QNTY` and the published percentage arithmetic; missing values fail closed. |

## NSE MII reference-master capture

NSE's daily report catalogue exposes the official CM and FO report lists through
`/api/daily-reports?key=CM` and `/api/daily-reports?key=FO`. The report records
carry the actual filename, archive directory, trading date, report key and reported
file size. The collector selects only the NSE-listed CM MII security file and the
NSE-exclusive FO MII contract file; it excludes the combined NSE/BSE CM file and
the NSE/BSE interop FO contract file.

The security and contract masters are separate datasets. The CM file is used for
symbol/series/ISIN reference reconciliation. The FO contract file describes listed
derivative contracts; stock F&O eligibility must be determined from active
individual-stock underlying contracts and their effective/expiry status, not from
the CM file or from a broad display label.

The first capture workflow stores compressed source files and a provenance manifest
as a GitHub Actions artifact (90-day retention). This is a validation/collection
stage, not yet a durable historical archive. Before relying on long-run point-in-time
history, move immutable raw files to a durable archive or implement a deliberate
content-addressed retention policy.

**Observed content-integrity failure (6 Nov 2026 catalogue date):** the captured CM
security file's maximum `UpdDt` decoded to 6 Nov 2016, ten years before its catalogue
date. In the FO contract file, non-test individual-stock contracts had expiries only
through 25 Jan 2017; the only 2026 stock-futures rows were dummy `NSETEST` symbols.
The collector now marks these payloads quarantined and the workflow fails its
semantic freshness gate while still uploading the raw evidence and manifest.
No F&O eligibility is derived from this capture. This discrepancy needs source-side
resolution or a separately validated feed before production use.

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


## UDiFF derivatives live-validation record

The official final UDiFF collector and workflow are implemented in
`src/fii_engine/nse_fo_bhavcopy.py` and
`.github/workflows/nse-udiff-fo-bhavcopy.yml`. The first live capture on
7 Nov 2026 validated the 6 Nov 2026 trade-date archive: 40,471 rows,
40,471 distinct contract IDs, all 34 observed columns recognized, 218
individual-stock underlyings and 6 index symbols. The immutable provenance
manifest is committed at
`data/raw/fno_bhavcopy/manifests/2026-11-06_9021b35dccd79f13.json`.
The original ZIP and parsed CSV are retained as a 90-day workflow artifact
while the long-term archive strategy is evaluated.


## NSE participant/FII report live-validation record

The participant-wise OI, participant-wise volume and FII derivatives statistics
collectors are implemented in
`src/fii_engine/nse_fo_aggregate_reports.py` and
`.github/workflows/nse-fo-aggregate-reports.yml`. First live capture:
[run 42355384757](https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42355384757),
trade date 6 Nov 2026. The normalized data and cross-source reconciliation
are documented in [NSE participant and FII report semantics](nse-fo-participant-reports.md).

The FII workbook OI reconciled exactly with participant FII long+short OI for
index futures, stock futures and stock options; index options differed by one
contract. This discrepancy is preserved. Neither source contains individual
stock FII positions.


## NSE-only FII/DII cash-flow live-validation record

The NSE-only daily API collector is implemented in
`src/fii_engine/nse_fii_dii.py` and
`.github/workflows/nse-fii-dii-cash-flows.yml`. First live capture:
[run 42355689812](https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42355689812),
trade date 6 Nov 2026. The response's net values reconciled exactly to
buy minus sell for both categories. The combined NSE/BSE/MSEI series and
final NSDL/CDSL FPI data remain separate sources.


## NSE cash-market price and delivery adapter

The cash-market adapter is implemented in `src/fii_engine/nse_cash_market.py`
with daily automation in `.github/workflows/nse-cash-market-price-delivery.yml`.
It selects both reports from the official NSE CM daily-report catalogue
(`/api/daily-reports?key=CM`) using the catalogue's filename, archive path
and trading date. It does not construct archive URLs or use an unofficial mirror.

The two datasets are kept separate:

- **CM UDiFF Common Bhavcopy Final:** market-wide security-level cash prices,
  traded volume, turnover, ISIN, symbol and series.
- **Full Bhavcopy and Security Deliverable data:** security-wise traded quantity,
  deliverable quantity and delivery percentage.

Both files must report the same trade date. The parsers validate required fields,
dates, numeric values, duplicate keys and delivery arithmetic. A failed or missing
source blocks publication; it is never converted to zero. Raw files and parsed
UDiFF CSV are uploaded as a 90-day workflow artifact. Compact immutable
provenance manifests are committed to the repository.

Live source validation is pending the first main-branch scheduled/manual run.
