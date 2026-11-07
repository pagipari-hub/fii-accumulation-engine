# NSE bulk and block deal API schema probe

## Official source and endpoint

- Page: https://www.nseindia.com/report-detail/display-bulk-and-block-deals
- Same-origin JavaScript asset: `/dist/js/sections/bulk-block-deals-short-selling.js`
- Historical endpoint: `/api/historicalOR/bulk-block-short-deals`
- Supported deal types: `bulk_deals`, `block_deals`
- Query parameters observed in the official page JavaScript: `optionType`, `from`, `to`, optional `symbol`; CSV download adds `csv=true`.

The source page's visible one-week range during inspection on 7 November 2026 was 30 October 2026 to 6 November 2026. The probe uses those page-displayed values in the exact date strings passed by the UI. It does not infer a full archive period.

## Probe design

`src/fii_engine/nse_deals_probe.py` bootstraps an NSE cookie session, then requests JSON and CSV for both bulk and block deal types over the displayed date range. It stores the raw response bodies and records:
- request URL and option type;
- HTTP status, content type and content disposition;
- retrieval timestamp, byte length and SHA-256;
- JSON top-level keys, columns, row count and sample rows;
- CSV header, row count and sample rows.

This is a discovery probe, not a production ingestion adapter. It does not classify client names as FII/FPI, infer beneficial ownership, or assign a stock-level score.

## Required next validation

Before production ingestion:
1. Inspect actual JSON/CSV schemas and reconcile both formats.
2. Establish transaction date and report revision semantics.
3. Verify whether the endpoint returns all records for the selected date range or applies a hidden limit/pagination.
4. Check duplicate/revised transactions and stable transaction identifiers.
5. Validate quantity, price, buy/sell side, client name, symbol and deal type fields.
6. Preserve raw disclosures and classify named clients separately from inferred institutional categories.

A bulk/block deal is named transaction evidence, but the disclosed client is not automatically an FII/FPI or beneficial owner.


## First live schema probe

GitHub Actions run:
https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42358048713

The official page displayed the range 30 Oct 2026 through 6 Nov 2026. The endpoint returned HTTP 200 for JSON and CSV for both deal types.

**CSV export (primary range dataset):**
- Bulk deals: 644 rows, 118 symbols, 207 distinct client names.
- Block deals: 36 rows, 6 symbols, 29 distinct client names.
- Both CSVs used eight columns: Date, Symbol, Security Name, Client Name, Buy / Sell, Quantity Traded, Trade Price / Wght. Avg. Price, Remarks.
- No exact duplicate rows were observed in this capture.

**JSON page response (cross-check only):**
- Bulk deals: 70 rows, all matching a subset of the 644-row CSV.
- Block deals: 36 rows, matching the 36-row CSV.
- The bulk JSON response is not complete for the requested range; it must not be used as the primary history source. The CSV download is the page's full-range export and its content-disposition filename explicitly contains the requested date range.

Bulk CSV rows by trade date: 30 Oct (88), 2 Nov (87), 3 Nov (147), 4 Nov (133), 5 Nov (93), 6 Nov (96). Block CSV rows: 30 Oct (12), 3 Nov (2), 4 Nov (2), 6 Nov (20).

The JSON payload includes `BD_DT_DATE` and a `BD_DT_ORDER` UTC timestamp. The CSV's explicit `Date` column is used as the transaction date. Do not substitute the UTC timestamp, which is the prior UTC calendar date for these India-local trade dates.

The raw probe files and schema manifest are preserved in artifact:
https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42358048713

## Production collector

`src/fii_engine/nse_bulk_block_deals.py` uses the official CSV export as the primary dataset and cross-checks the JSON page response as a subset. It stores immutable per-trade-date normalized snapshots, raw-response hashes and provenance manifests. The daily workflow queries an overlapping seven-calendar-day range so a missed run can be recovered on the next run and later source revisions produce new content-addressed snapshots. On Saturday/Sunday manual runs, the default range ends on the previous Friday (rather than the weekend date) so the latest completed weekday remains inside the query.

A client name is a disclosed counterparty label, not a verified investor classification. No client is automatically labelled FII/FPI, mutual fund, promoter or beneficial owner from its name alone. Bulk and block deals are separate evidence classes and BUY/SELL observations are retained individually; they are not silently netted into FII flows.
