# Daily Nifty 500 stock evidence matrix

## Purpose

`src/fii_engine/daily_stock_features.py` joins validated daily source files into
one stock-level evidence ledger. It is an input layer for the scoring engine,
not a production FPI ranking.

The output preserves every current Nifty 500 constituent, including stocks
with no reported activity or unavailable source data.

## Source joins

- **Universe:** official Nifty 500 snapshot; ISIN and symbol uniqueness are required.
- **F&O membership:** current official NSE stock-underlying status joined by symbol
  and checked against the universe ISIN. `not_eligible` is distinct from `unknown`.
- **Cash market:** official NSE CM UDiFF stock rows (`STK`) joined to the
  universe by the composite key (ISIN, constituent series). The same ISIN may
  appear in more than one NSE series, so ISIN alone is not assumed unique in
  the daily CM file. This preserves BE and REIT/RR constituents as well as EQ.
- **Delivery:** official NSE delivery file joined by current symbol and the
  constituent's exact series (EQ, BE, RR, etc.). A dash in delivery
  quantity/percentage remains unreported, not zero.
- **Stock derivatives:** official NSE UDiFF F&O rows. The nearest unexpired
  stock-futures expiry is selected; nearest-expiry stock options are aggregated
  by CE/PE with PCR. Futures price/OI classification is market-wide contract
  activity, not FII attribution.
- **Bulk/block deals:** immutable daily snapshots are joined by current symbol.
  Client names and buy/sell activity are retained as disclosed-event annotations.
  They are not classified as FPI unless a separate verified counterparty mapping
  is built.

## Missingness and date controls

- The matrix requires a 480–520-row universe and preserves all its rows.
- Cash UDiFF and delivery must share a trade date and be marked final.
- F&O data must be final and match the cash trade date before the two are
  combined. A date mismatch makes the F&O family unavailable for that run.
- Source paths and hashes are checked against their manifests.
- Missing source files produce unavailable states; they are never interpreted
  as zero activity.
- A verified cash-only stock has F&O fields marked `not_applicable`. Unknown
  F&O membership remains unavailable.
- Deal rows outside the captured date window are excluded. A stale window is
  labelled stale, not current.
- The current F&O membership retrieval time is retained; the NSE feed does not
  provide an exact effective date, so none is invented.

## Automation

`src/fii_engine/daily_pipeline.py` runs the official Nifty 500, current F&O
membership, FII/DII, cash-market, UDiFF derivatives, participant/FII derivatives
reports and bulk/block deal collectors. A failure in one collector does not
suppress independent sources; the run status is retained and stale source files
are ignored when their collector did not succeed.

GitHub Actions runs the pipeline on weekdays at 23:00 IST and supports manual
dispatch. It uploads the raw/normalized run inputs and the evidence matrix as a
90-day artifact, then commits only the fused daily matrix. Individual source
workflows own and persist their respective source snapshots and provenance
manifests, avoiding concurrent commits to the same paths. A partial run is
labelled partial; a run with no current cash or F&O market data does not claim a
valid market snapshot.

## Output

- `data/snapshots/daily_stock_features/daily_stock_features_<date>_<hash>.csv`
- `data/raw/daily_stock_features/daily_stock_features_current.csv`
- `data/raw/daily_stock_features/daily_stock_features_current_manifest.json`

The manifest records source paths, SHA-256 hashes, retrieval times, row counts,
status counts and limitations.

## Scoring readiness

The matrix intentionally does not publish scores yet:

- Quarterly FPI Category I/II ownership history has not yet been joined.
- Disclosed deals are not FPI-attributed until counterparties are independently
  classified.
- Cash-market scoring awaits rolling delivery/volume/price baselines.
- Stock-derivatives scoring awaits rolling stock-level OI history.

The scoring core will consume normalized family observations only after these
requirements are met. Market-wide FII/DII cash flows and participant aggregates
remain separate market context, not stock-specific observations.
