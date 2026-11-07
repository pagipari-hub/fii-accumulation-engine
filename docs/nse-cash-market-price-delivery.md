# NSE cash-market prices and security delivery

## Official sources

Both files are discovered through the official NSE capital-market daily-report catalogue:

- Report page: https://www.nseindia.com/all-reports
- Catalogue: https://www.nseindia.com/api/daily-reports?key=CM
- Archive host accepted by the collector: `nsearchives.nseindia.com`

The adapter selects report records by their official display names and validates the filename date against the catalogue trading date. The download directory and filename come from the catalogue itself. No archive URL is guessed.

## Dataset A: CM UDiFF Common Bhavcopy Final

Expected filename pattern:
`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`

The ZIP must contain exactly one non-empty CSV. The parser requires UDiFF date, segment, instrument/security identifiers, ISIN, symbol, series, OHLC, traded quantity, turnover and trade count fields. It retains all columns in the parsed source CSV and records the exact schema/header hash.

This is the official final cash-market security-level price/volume source. It is not participant-specific and does not identify who bought or sold a security. Series remain explicit; downstream Nifty 500 joins should use ISIN with symbol and series checks, not symbol alone.

## Dataset B: Full Bhavcopy and Security Deliverable data

Expected filename pattern:
`sec_bhavdata_full_DDMMYYYY.csv`

The parser requires symbol, series, report date, OHLC/average prices, total traded quantity, turnover, trade count, deliverable quantity and delivery percentage.

Validation includes:
- report date equals the UDiFF trade date;
- unique symbol/series/date keys;
- required price and delivery fields are present;
- deliverable quantity is non-negative and no greater than traded quantity;
- delivery percentage agrees with deliverable quantity divided by traded quantity within a rounding tolerance;
- NSE `-` sentinels remain unavailable, never zero. A traded row with both delivery fields unavailable is retained for price/volume use and counted as `not_reported_with_trades`, including counts by series. Partial delivery fields remain a validation failure.

Delivery percentage is a market microstructure observation. It is not proof of institutional buying, FII ownership or beneficial ownership. It must be interpreted alongside price/volume context and other independent evidence.

## Provenance and automation

The adapter writes raw source bytes, a parsed UDiFF CSV, content hashes and per-source manifests. The current manifest records both catalogue records and the common trade date. The GitHub Actions workflow runs weekdays at 21:30 IST and can also be manually dispatched.

Raw/parsed daily files and the current manifest are uploaded as a 90-day artifact. Compact immutable manifests are committed to the repository. A schema change, missing report, date mismatch, duplicate security key or invalid delivery arithmetic fails the run rather than silently publishing incomplete data.

## First live validation

GitHub Actions run:
https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42357169361

Trade date: 6 November 2026.

**CM UDiFF final bhavcopy**
- 3,715 rows, 34 columns and 3,713 distinct symbols.
- Every row was instrument type `STK`.
- Unique instrument keys passed; two additional rows represent repeated symbols across different series/instrument records.
- Schema SHA-256: `ec79fe2b3450a97e32b52a6b80c0260159acd8503211d32b7089d70ed04f6349`.

**Full Bhavcopy and Security Deliverable data**
- 3,559 rows, 15 columns and 3,559 distinct symbols.
- 3,325 rows had numeric delivery observations.
- 234 rows had unavailable delivery fields: 201 BE series and 33 BZ series. These rows remain valid price/volume observations but delivery is N/A, not zero.
- The 2,746 EQ-series rows all had delivery observations in this capture.
- Schema SHA-256: `a1739ff676a12f307310f2799e661599ca138a8ddbe47032b0ae15876a8e9f16`.

Full project tests: 115 passed. Original files, parsed UDiFF CSV, current manifest and immutable source manifests were uploaded as artifact:
https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42357169361

This is a first point-in-time validation, not yet a historical backfill. The scheduled workflow must continue to collect each trading day to build a reliable time series.
