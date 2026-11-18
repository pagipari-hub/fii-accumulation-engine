"""Probe symbol-specific NSE shareholding master history without changing production data."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote

from fii_engine.nse_shareholding_probe import BASE, MASTER_URL, PAGE, _get

DEFAULT_SYMBOLS = ["RELIANCE", "HDFCBANK", "ABBOTINDIA", "ALKEM", "MCX"]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _records(decoded: object) -> list[dict]:
    if isinstance(decoded, list):
        return [row for row in decoded if isinstance(row, dict)]
    if isinstance(decoded, dict):
        for key in ("data", "records", "Table", "shareholdingPattern", "shareholdingData"):
            value = decoded.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [decoded]
    return []


def probe(
    output_dir: Path,
    symbols: list[str] | None = None,
    delay: float = 0.3,
    from_date: str = "01-07-2026",
    to_date: str = "11-11-2026",
) -> dict:
    symbols = [symbol.strip().upper() for symbol in (symbols or DEFAULT_SYMBOLS)]
    output_dir.mkdir(parents=True, exist_ok=True)
    _, bootstrap = _get(PAGE, BASE, "text/html,*/*")
    market_url = f"{MASTER_URL}&from_date={quote(from_date)}&to_date={quote(to_date)}"
    market_wide = {"query_url": market_url, "from_date": from_date, "to_date": to_date}
    try:
        market_payload, market_response = _get(market_url, PAGE, "application/json,*/*")
        market_digest = _sha256(market_payload)
        market_raw_path = output_dir / f"nse_shareholding_market_history_{market_digest[:16]}.json"
        market_raw_path.write_bytes(market_payload)
        market_decoded = json.loads(market_payload.decode("utf-8-sig"))
        market_rows = _records(market_decoded)
        market_symbols = sorted({
            str(row.get("symbol", row.get("SYMBOL", ""))).strip().upper()
            for row in market_rows
            if row.get("symbol", row.get("SYMBOL", ""))
        })
        market_date_fields = sorted({
            key for row in market_rows for key in row
            if any(token in key.lower() for token in ("date", "period", "quarter"))
        })
        market_wide.update({
            **market_response,
            "status": "captured",
            "response_sha256": market_digest,
            "raw_path": str(market_raw_path),
            "row_count": len(market_rows),
            "unique_symbol_count": len(market_symbols),
            "symbols": market_symbols,
            "record_keys": sorted(market_rows[0]) if market_rows else [],
            "date_fields": market_date_fields,
            "date_samples": [
                {key: row.get(key) for key in market_date_fields if row.get(key) not in (None, "")}
                for row in market_rows[:10]
            ],
        })
    except Exception as exc:
        market_wide.update({"status": "probe_error", "error": str(exc)})
    observations = []
    for index, symbol in enumerate(symbols):
        queries = [
            ("symbol_history", f"{MASTER_URL}&symbol={quote(symbol)}"),
            (
                "date_range",
                f"{MASTER_URL}&from_date={quote(from_date)}&to_date={quote(to_date)}&symbol={quote(symbol)}",
            ),
        ]
        query_results = []
        for query_type, url in queries:
            record = {"symbol": symbol, "query_type": query_type, "query_url": url}
            try:
                payload, response = _get(url, PAGE, "application/json,*/*")
                digest = _sha256(payload)
                raw_path = output_dir / f"nse_shareholding_history_{symbol}_{query_type}_{digest[:16]}.json"
                raw_path.write_bytes(payload)
                decoded = json.loads(payload.decode("utf-8-sig"))
                rows = _records(decoded)
                date_fields = sorted({
                    key for row in rows for key in row
                    if any(token in key.lower() for token in ("date", "period", "quarter"))
                })
                attachment_fields = sorted({
                    key for row in rows for key in row
                    if any(token in key.lower() for token in ("xbrl", "attach", "file", "url", "link"))
                })
                date_samples = [
                    {key: row.get(key) for key in date_fields if row.get(key) not in (None, "")}
                    for row in rows[:10]
                ]
                record.update({
                    **response,
                    "status": "captured",
                    "response_sha256": digest,
                    "raw_path": str(raw_path),
                    "row_count": len(rows),
                    "record_keys": sorted(rows[0]) if rows else [],
                    "date_fields": date_fields,
                    "attachment_fields": attachment_fields,
                    "date_samples": date_samples,
                    "records": rows,
                })
            except Exception as exc:
                record.update({"status": "probe_error", "error": str(exc)})
            query_results.append(record)
            time.sleep(delay)
        observations.append({"symbol": symbol, "queries": query_results})
        if index + 1 < len(symbols):
            time.sleep(delay)
    report = {
        "publisher": "National Stock Exchange of India",
        "endpoint": MASTER_URL,
        "symbol_specific_history_probe": True,
        "date_range": {"from_date": from_date, "to_date": to_date},
        "session_bootstrap": bootstrap,
        "market_wide_history": market_wide,
        "candidate_symbols": symbols,
        "observations": observations,
        "row_counts": {
            row["symbol"]: {query["query_type"]: query.get("row_count", 0) for query in row["queries"]}
            for row in observations
        },
        "symbols_with_multiple_records": [
            row["symbol"] for row in observations
            if any(query.get("row_count", 0) > 1 for query in row["queries"])
        ],
        "scope_boundary": (
            "Read-only endpoint capability probe. Captured records are not ingested into the ownership master "
            "until reporting periods, identity, raw filing links and XBRL hashes are validated."
        ),
    }
    report_path = output_dir / "nse_shareholding_history_probe_manifest.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--from-date", default="01-07-2026")
    parser.add_argument("--to-date", default="11-11-2026")
    args = parser.parse_args()
    report = probe(args.output_dir, args.symbols, args.delay, args.from_date, args.to_date)
    print(json.dumps({
        "symbols": len(report["candidate_symbols"]),
        "market_wide_row_count": report["market_wide_history"].get("row_count", 0),
        "market_wide_unique_symbol_count": report["market_wide_history"].get("unique_symbol_count", 0),
        "row_counts": report["row_counts"],
        "symbols_with_multiple_records": report["symbols_with_multiple_records"],
    }, indent=2))


if __name__ == "__main__":
    main()
