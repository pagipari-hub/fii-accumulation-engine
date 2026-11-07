# Official Nifty 500 universe ingestion

## Source and collection

The collector starts at the official NSE Indices Nifty 500 page:

- https://www.nseindia.com/static/products-services/indices-nifty500-index

It reads the explicitly labelled **Download List of Nifty 500 Stocks (.csv)** link and accepts the download only from the official \`nsearchives.nseindia.com\` host. It establishes an NSE homepage session first and reuses the cookie jar for the page and CSV request. The download URL is discovered from the live page; it is not guessed or hard-coded.

## Validation

The adapter validates the source header and required fields, requires exactly 500 rows, rejects duplicate symbols and ISINs, and fails closed on schema drift or incomplete downloads. It preserves the original bytes and writes a normalized CSV for downstream processing.

## Date and provenance semantics

The page's displayed **Updated on** date is recorded as \`source_page_updated_on\`. It is not treated as a constituent membership effective date. The manifest records retrieval time in UTC and SHA-256 hashes of original and normalized data.

No \`effective_from\` membership intervals are fabricated from a page-update date. Those intervals require separately verified official effective-date evidence, such as index-rebalance notices. Until that evidence is ingested, this output is a dated source snapshot, not reconstructed point-in-time membership history.

## Files

- \`data/raw/universe/nifty500_source_<page-date>_<hash>.csv\`: immutable original NSE download.
- \`data/raw/universe/nifty500_current.csv\`: current normalized constituent list.
- \`data/snapshots/universe/nifty500_<page-date>.csv\`: immutable normalized snapshot.
- \`data/snapshots/universe/nifty500_<page-date>.json\`: provenance manifest.

If constituent content changes without a new page-update date, the collector stops instead of overwriting the prior dated snapshot. A weekly GitHub Actions check commits only changed files. Workflow success alone is not proof of a valid collection; the adapter's row/schema validations must pass.
