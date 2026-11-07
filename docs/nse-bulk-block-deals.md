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
