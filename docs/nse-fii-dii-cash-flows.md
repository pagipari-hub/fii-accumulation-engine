# NSE-only FII/FPI and DII cash-market flows

## Source identity

- Official page: https://www.nseindia.com/reports/fii-dii
- Official NSE API: https://www.nseindia.com/api/fiidiiTradeReact
- Scope: NSE-only capital-market segment
- Currency/unit: INR crore
- Frequency: daily
- FII/FPI publication status: provisional, subject to custodial confirmation and modifications
- DII status: exchange-reported using member trading codes/client-category classification

The NSE page separately presents NSE-exclusive data and combined NSE/BSE/MSEI data. This collector captures only the NSE-exclusive API. The combined series has a separate source-register entry and must not be mixed with this dataset.

## Data contract

The live API returns rows with these fields:

- `date`
- `category` (FII/FPI or DII; preserve the raw label)
- `buyValue`
- `sellValue`
- `netValue`

The parser validates the date, required fields, recognized category, uniqueness of each date/category pair and `netValue = buyValue - sellValue` within one paisa of a crore. Missing values are errors, never zeros.

Normalized rows explicitly include `scope=NSE_only`, `segment=capital_market`, `unit=crore`, and publication status. FII/FPI is labelled provisional; DII is not incorrectly labelled as the final FPI series.

## Historical preservation

The API response may expose only the latest date or a limited rolling window. The collector snapshots every date actually returned on each run. Per-date snapshots and manifests are content-addressed and committed, so later daily runs build a durable time series and revised provisional data becomes a separate version rather than overwriting history.

The raw JSON response and current manifest are uploaded as a 90-day workflow artifact. The normalized per-date CSV snapshots and immutable manifests are committed to the repository.

## First live validation

GitHub Actions run:
https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42355689812

Trade date: 6 November 2026. The first API response contained one trade date and two category rows:

| Category | Buy (₹ Cr) | Sell (₹ Cr) | Net (₹ Cr) | Status |
|---|---:|---:|---:|---|
| FII/FPI | 30,455.63 | 16,705.81 | +13,749.82 | Provisional |
| DII | 18,084.32 | 24,053.02 | -5,968.70 | Exchange-reported |

Both net arithmetic checks matched exactly. The raw response SHA-256 is
`b5851617af7a361ae5f8bc8de545b2148e26b1dd67d1697d4b19fe4d888c6417`.
The normalized daily snapshot SHA-256 is
`79816b7098dca86f58a518e2b2fe1f317c0b26b81da40616a32113036ac05b32`.

The first response contained only one trade date. The scheduled daily collector is therefore important for building the history; this initial capture is not a historical backfill.

## Source caveats

- NSE FII/FPI values are provisional and may be revised after custodial confirmation/modifications.
- Physical-settlement obligations on expiry days may be included in FII/FPI figures.
- Final FPI data must be sourced separately from NSDL/CDSL.
- The combined NSE/BSE/MSEI dataset is separate and not included here.
- Market-wide FII/DII flows do not identify which individual stocks were bought or sold.

## Next source

The combined NSE/BSE/MSEI FII/FPI and DII CSV is registered separately. Its exact file schema, publication status and date semantics must be validated before ingestion.
