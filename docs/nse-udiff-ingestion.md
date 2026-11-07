# NSE UDiFF equity-derivatives bhavcopy ingestion

## Official source contract

- Catalogue page: https://www.nseindia.com/all-reports-derivatives
- NSE report catalogue API: https://www.nseindia.com/api/daily-reports?key=FO
- Selected report key: `FO-UDIFF-BHAVCOPY-CSV`
- Publisher: National Stock Exchange of India
- Final archive host: `nsearchives.nseindia.com`
- Archive filename pattern: `BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip`
- Finality: final UDiFF common bhavcopy.

The collector reads the exact filename, archive directory, and trading date from NSE's own report catalogue. It does not construct a guessed daily archive URL. A shared NSE cookie session is established by requesting the homepage and derivatives reports page before the catalogue API and archive.

NSE states on the report page that the older F&O common bhavcopy formats were discontinued effective 8 July 2024; this pipeline uses the final UDiFF ZIP. The report catalogue also lists participant-wise OI/volume, FII derivatives statistics, MWPL-related files, the security-in-ban-period report, and the NSE-exclusive MII contract master as separate reports. They are not merged into the bhavcopy.

## UDiFF contract fields

The parser requires the core contract, price, volume and OI tags:

- Identity/date: `TradDt`, `BizDt`, `Sgmt`, `Src`, `FinInstrmTp`, `FinInstrmId`, `TckrSymb`, `XpryDt`, `FinInstrmNm`.
- Option contract details: `StrkPric`, `OptnTp`.
- Prices: `OpnPric`, `HghPric`, `LwPric`, `ClsPric`, `LastPric`, `PrvsClsgPric`, `UndrlygPric`, `SttlmPric`.
- Activity: `OpnIntrst`, `ChngInOpnIntrst`, `TtlTradgVol`, `TtlTrfVal`, `TtlNbOfTxsExctd`, `NewBrdLotQty`.

The complete source header is retained in the manifest. Additional UDiFF identity/reserved fields remain in the original CSV and are not discarded. Instrument types currently recognized are `STF`, `STO`, `IDF`, and `IDO`; a new code stops ingestion pending review.

The pipeline retains original numeric strings. It does not yet convert traded volume to lots or underlying quantity, infer FII ownership, or interpret turnover units for scoring. Those semantics must be checked against NSE's format documentation and independent reconciliations before analytics use.

## Validation and provenance

The collector:

1. Bootstraps one NSE session and reads the official FO report catalogue.
2. Selects the newest final UDiFF record from the catalogue's current/previous report groups.
3. Validates the report key, filename date, archive host and trading date.
4. Downloads the original ZIP, validates ZIP CRC and requires exactly one non-empty CSV.
5. Checks UTF-8, duplicate headers, required fields, dates, FO/NSE segment identity, recognized instrument types, unique contract IDs, numeric fields, option type/strike consistency and a single business date.
6. Writes the original ZIP before the parsed CSV and records SHA-256 hashes, catalogue metadata, retrieval timestamp, row counts, instrument-type counts and schema hash.

Missing fields, unknown instrument types, malformed rows, duplicates, date mismatches, empty archives and schema drift fail the run. Missing data is never converted to zero.

## Storage and current limitation

The workflow runs on weekdays at 21:15 IST and can be manually dispatched. It uploads the original ZIP, parsed CSV and run manifest as a 90-day GitHub Actions artifact. It commits only small, content-addressed provenance manifests under `data/raw/fno_bhavcopy/manifests/`.

This is the initial live-ingestion validation stage, not yet a permanent bulk archive. The official NSE archive remains the historical source of record. Before production-scale historical backfill and analytics, decide on a durable low-cost archive strategy and verify long-term file availability. Repeated captures of the same trade-date archive are content-addressed; a revised archive becomes a separate version.

## Attribution boundary

The UDiFF bhavcopy contains market-wide contract-level trades, prices, volume and OI. It does not identify which participant category created the OI. Participant-wise OI/volume and FII derivatives statistics remain separate aggregate datasets. No stock-level FII attribution is inferred from this file.

## References

- [NSE All Reports – Derivatives](https://www.nseindia.com/all-reports-derivatives)
- [NSE F&O report catalogue API](https://www.nseindia.com/api/daily-reports?key=FO)
- [NSE circular on UDiFF format transition](https://nsearchives.nseindia.com/content/circulars/INVG61787.pdf)


## First live validation — 6 November 2026

The first successful main-branch capture completed in GitHub Actions run
[42354484351](https://github.com/pagipari-hub/fii-accumulation-engine/actions/runs/42354484351)
on 7 November 2026.

- Catalogue report key: `FO-UDIFF-BHAVCOPY-CSV`
- Official archive: `BhavCopy_NSE_FO_0_0_0_20261106_F_0000.csv.zip`
- Archive size: 1,282,557 bytes; SHA-256:
  `9021b35dccd79f13d642bee45dc0fc8660122fadcb673158c6969c43c1fbf8d5`
- Parsed CSV: 7,381,195 bytes; SHA-256:
  `03d56ecc6e0436dd55e870f55f7200f0f6b26c0d3a574ae39c33be446837d1df`
- Rows and distinct contract IDs: 40,471 each
- Instrument types: 649 `STF`, 33,179 `STO`, 18 `IDF`, 6,625 `IDO`
- Distinct underlying symbols across index and stock contracts: 224; stock futures/options symbols: 218
- All 34 observed source columns matched the known UDiFF header; no unrecognized columns.
- `TradDt` and `BizDt` both matched 2026-11-06.

This is one live date/schema validation, not yet a historical backfill or proof of long-term archive durability. Volume and turnover unit semantics remain intentionally uninterpreted by the ingestion layer.
