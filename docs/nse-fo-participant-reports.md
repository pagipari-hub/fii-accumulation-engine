# NSE participant-wise derivatives and FII statistics

## Official report sources

All three reports are selected from the official NSE FO daily-report catalogue:
https://www.nseindia.com/api/daily-reports?key=FO

| Dataset | Catalogue report key | Format | What it establishes |
|---|---|---|---|
| Participant-wise open interest | `FO-PARTICIPANTWISE-OI` | CSV | End-of-day long/short contract counts by participant category and index/stock futures/options group |
| Participant-wise trading volume | `FO-PARTICIPANTWISE-TRADING-VOL` | CSV | Daily traded contract counts by participant category and index/stock futures/options group |
| FII derivatives statistics | `FO-FII-DERIVATIVE-STAT` | Legacy XLS | Aggregate FII buy/sell contract counts and amounts plus end-of-day OI; index-underlying detail and all-stock totals |

The collector uses the report catalogue's exact filename, archive directory and trading date. It does not construct a guessed archive URL. The original exchange file is written before parsing. Raw files, normalized CSVs and a run manifest are uploaded as a 90-day workflow artifact; content-addressed SHA-256 provenance manifests are committed to the repository.

## Participant CSV structure

The participant reports contain a title/preamble line followed by a 15-column table. The title line is irregularly quoted by the source, so the parser handles it separately and validates its as-of date against the catalogue trading date.

The participant categories are `Client`, `DII`, `FII`, `Pro` and `TOTAL`. The normalized dataset excludes the `TOTAL` row from participant records because it is a reconciliation total, not another participant category.

The 12 component columns describe:
- Index futures: long and short
- Stock futures: long and short
- Index options: call/put long and call/put short
- Stock options: call/put long and call/put short

The source labels all values as number of contracts. The normalized CSV retains `source_side` as `long` or `short`; it does not relabel these as stock-specific buys or sells. Each participant report produces 48 normalized records: four participant categories × 12 component measures.

Reported total columns and the `TOTAL` row are preserved separately. Reconciliation differences are recorded rather than silently corrected. The 6 Nov 2026 OI report contains one-contract differences in a few totals; the volume report reconciles exactly.

## FII derivatives statistics workbook

The workbook has one sheet and seven columns. It reports:
- Buy: number of contracts and amount in crores
- Sell: number of contracts and amount in crores
- End-of-day open interest: number of contracts and amount in crores

It contains four section totals:
- Index futures
- Index options
- Stock futures
- Stock options

Index futures and options also contain underlying-level rows (e.g. BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTY, NIFTYFPI and NIFTYNXT50). Stock futures and stock options are aggregate totals only; this workbook does not identify FII positions in individual stocks.

The workbook notes say:
- Both buy and sell positions have been considered.
- Buy/sell options value = strike price × quantity.
- Buy/sell futures value = traded price × quantity.
- End-of-day options value = underlying close price × quantity.
- End-of-day futures value = daily settlement price × quantity.

Section totals are retained alongside detail rows with a `record_level` field. Do not add section totals to their own underlying details. Amount reconciliation differences are recorded; the 6 Nov 2026 index-futures amount sums differ by 0.01 crore due to displayed precision.

## Independent FII OI cross-check

The collector compares FII long + short participant OI against the FII workbook's end-of-day OI contracts for four instrument groups. This is a cross-source validation, not a stock-level attribution method.

First live capture for trade date 6 Nov 2026:

| Instrument group | Participant OI long + short | FII workbook end-of-day OI | Difference |
|---|---:|---:|---:|
| Index futures | 197,728 | 197,728 | 0 |
| Stock futures | 6,131,665 | 6,131,665 | 0 |
| Index options | 4,158,867 | 4,158,866 | -1 |
| Stock options | 1,359,538 | 1,359,538 | 0 |

Three groups reconcile exactly; index options differ by one contract. The source values are preserved unchanged.

## First live validation

GitHub Actions run:
https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42355384757

Trade date: 6 November 2026.

- Participant OI: 48 normalized records; maximum source reconciliation difference 1 contract.
- Participant volume: 48 normalized records; exact component/total reconciliation.
- FII statistics: 16 normalized records (4 section totals + 12 index-underlying details).
- Cross-source FII OI: three exact group matches and one one-contract difference.
- Full project tests: 92 passed.
- Raw source files, normalized CSVs and current manifest captured; immutable parser-version-2 manifests committed.

## Attribution boundaries

These datasets are useful for market-wide participant positioning and aggregate FII derivatives exposure. They do not contain FII positions for each individual stock. Participant-wise OI/volume is reported by participant category and instrument group, while the FII workbook provides index-underlying detail and aggregate stock futures/options totals. Do not infer stock-specific FII accumulation from either source.

For stock-level F&O activity, combine UDiFF contract data with these aggregate reports only as market context. For stock-specific FII evidence, use named cash-market transactions or periodic FPI shareholding disclosures, and grade indirect evidence separately.
