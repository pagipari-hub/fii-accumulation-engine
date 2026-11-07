# NSE current individual-stock F&O underlyings

## Official source

- Page: https://www.nseindia.com/products-services/equity-derivatives-list-underlyings-information
- API observed in the page's inline JavaScript: https://www.nseindia.com/api/underlying-information

The page calls `/api/underlying-information` and reads `data.IndexList` and
`data.UnderlyingList`. The latter is the current list of individual-stock
underlyings; the index-underlying list is kept separate.

## Validation and provenance

The adapter requires the page to continue referencing the API, validates both
lists, checks nonblank symbol/name fields, unique symbols and serial numbers, and
requires the stock list to remain within a broad safety range. It preserves the
original JSON and normalized CSV as content-addressed snapshots.

The API does not supply an explicit effective date. The manifest therefore records
retrieval time as a point-in-time observation and does not invent a membership
effective date. The official Introduction/Exclusion Tracker provides historical
change events and circular references, but it is not a complete baseline by itself.

## Live validation (7 November 2026)

The official API returned 6 index underlyings and 218 individual-stock underlyings.
The normalized snapshot is content-addressed and has passed live schema, uniqueness,
and serial-sequence validation. The API does not provide a precise effective date.

## Data boundary

The current list is authoritative for current individual-stock underlying
membership. It is not a replacement for contract-level data such as expiry,
instrument type, lot size, or daily open interest. The Introduction/Exclusion
tracker provides effective-dated changes; its `ZOMATO` introduction reconciles to
current `ETERNAL` through NSE F&O circular 54/2025 (effective 9 April 2025). The separate NSE FO MII contract
master remains quarantined because the captured file's internal dates were stale
relative to its catalogue trading date.

## Files

- `data/raw/fno_underlyings/fno_underlyings_source_<hash>.json`: immutable API response.
- `data/raw/fno_underlyings/fno_underlyings_current.csv`: current normalized index + stock list.
- `data/snapshots/fno_underlyings/fno_underlyings_<hash>.csv`: immutable normalized snapshot.
- `data/snapshots/fno_underlyings/fno_underlyings_<hash>.json`: provenance manifest.

## Historical reconciliation

The Introduction/Exclusion workbook is archived as an immutable XLSX plus a
content-addressed manifest. Each capture parses merged effective-date and circular
link cells; the daily F&O underlying snapshot re-parses the archived workbook using
the current India date and compares the latest effective action for each tracked
symbol with current membership.

Official symbol aliases are applied only from their NSE-published effective date.
The current alias registry includes `ZOMATO -> ETERNAL`, effective 9 April 2025,
from NSE F&O circular 54/2025. This resolves the sole apparent tracker/current-list
mismatch in the 7 November 2026 validation.

The tracker is not the complete baseline: symbols without tracker events are
retained as baseline members if present in the current official list. Future-dated
exclusions are not applied before their effective dates. Any unexplained mismatch
or unknown effective date is recorded and blocks the scheduled membership refresh.
