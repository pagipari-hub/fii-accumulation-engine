# Official Nifty 500 universe ingestion

## Source and collection

The collector uses the official NSE Indices Nifty 500 page and its published constituent CSV:

- Page: https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-500
- CSV: https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv

The source page states that data is as on the last trading day of the previous month. The collector records that statement verbatim; it does not manufacture a precise as-of date from it.

## Validation

The adapter validates the source header and required fields, enforces a 480–520 row safety range (the published Nifty 500 factsheet has shown constituent counts above 500), rejects duplicate symbols and ISINs, and fails closed on schema drift or incomplete downloads. It preserves the original bytes and writes a normalized CSV for downstream processing.

## Date and provenance semantics

The page's freshness statement is recorded as source metadata. It is not treated as a constituent membership effective date. The manifest records retrieval time in UTC and SHA-256 hashes of original and normalized data.

No effective membership intervals are fabricated from a monthly freshness statement. Those intervals require separately verified official effective-date evidence, such as index-rebalance notices. Until that evidence is ingested, this output is a dated source snapshot, not reconstructed point-in-time membership history.

## Files

- \`data/raw/universe/nifty500_source_<hash>.csv\`: immutable original NSE Indices download.
- \`data/raw/universe/nifty500_current.csv\`: current normalized constituent list.
- \`data/snapshots/universe/nifty500_<normalized-hash>.csv\`: immutable normalized snapshot.
- \`data/snapshots/universe/nifty500_<normalized-hash>.json\`: provenance manifest.

Snapshots are content-addressed. Repeated collection of unchanged data is idempotent; changed content creates a new immutable snapshot. A weekly GitHub Actions check commits only changed files. Workflow success alone is not proof of a valid collection; the adapter's source, row-count and schema validations must pass.
