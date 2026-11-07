"""Capture the official NSE current equity-derivatives underlying list."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

from fii_engine.nse_fo_tracker import parse_tracker_events

SOURCE_PAGE = (
    "https://www.nseindia.com/products-services/"
    "equity-derivatives-list-underlyings-information"
)
API_URL = "https://www.nseindia.com/api/underlying-information"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
MIN_STOCK_UNDERLYINGS, MAX_STOCK_UNDERLYINGS = 150, 300
COLUMNS = ("segment", "serial_number", "symbol", "underlying")
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))


def _get(url: str, referer: str, accept: str, timeout: int = 45) -> bytes:
    request = Request(
        url,
        headers={**HEADERS, "Accept": accept, "Referer": referer},
    )
    try:
        with HTTP.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"unexpected HTTP {response.status} for {url}")
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"NSE request failed for {url}: {exc}") from exc


def parse_underlying_payload(payload: bytes) -> tuple[list[dict], list[dict]]:
    try:
        body = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE underlying-information API did not return valid JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise ValueError("NSE underlying-information API schema drift: missing data object")
    data = body["data"]
    indexes, stocks = data.get("IndexList"), data.get("UnderlyingList")
    if not isinstance(indexes, list) or not indexes:
        raise ValueError("NSE underlying-information API omitted IndexList")
    if not isinstance(stocks, list) or not stocks:
        raise ValueError("NSE underlying-information API omitted UnderlyingList")
    if not MIN_STOCK_UNDERLYINGS <= len(stocks) <= MAX_STOCK_UNDERLYINGS:
        raise ValueError(
            f"individual-stock underlying count {len(stocks)} outside safety range "
            f"{MIN_STOCK_UNDERLYINGS}-{MAX_STOCK_UNDERLYINGS}"
        )

    def validate(rows: list, segment: str) -> list[dict]:
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{segment}: non-object row in source response")
            symbol = row.get("symbol")
            name = row.get("underlying")
            serial = row.get("serialNumber")
            if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip():
                raise ValueError(f"{segment}: invalid symbol {symbol!r}")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{segment}: invalid underlying name for {symbol!r}")
            if isinstance(serial, bool) or not isinstance(serial, int) or serial < 1:
                raise ValueError(f"{segment}: invalid serialNumber for {symbol!r}")
            normalized.append({
                "segment": segment,
                "serial_number": serial,
                "symbol": symbol,
                "underlying": " ".join(name.split()),
            })
        symbols = [row["symbol"] for row in normalized]
        serials = [row["serial_number"] for row in normalized]
        if len(symbols) != len(set(symbols)):
            raise ValueError(f"{segment}: duplicate symbols in official underlying list")
        if len(serials) != len(set(serials)):
            raise ValueError(f"{segment}: duplicate serial numbers in official underlying list")
        if sorted(serials) != list(range(1, len(serials) + 1)):
            raise ValueError(f"{segment}: serial numbers are not a complete 1..N sequence")
        return sorted(normalized, key=lambda row: row["serial_number"])

    return validate(indexes, "index"), validate(stocks, "individual_stock")


def reconcile_tracker_events(
    events: list[dict],
    current_symbols: set[str],
    aliases: list[dict],
    as_of_date: date,
) -> dict:
    alias_map = {}
    aliases_applied = []
    for alias in aliases:
        old_symbol = alias.get("old_symbol")
        new_symbol = alias.get("new_symbol")
        effective = date.fromisoformat(alias["effective_date"])
        if not old_symbol or not new_symbol or old_symbol == new_symbol:
            raise ValueError(f"invalid symbol alias record: {alias!r}")
        if effective <= as_of_date:
            if old_symbol in alias_map and alias_map[old_symbol] != new_symbol:
                raise ValueError(f"conflicting symbol aliases for {old_symbol}")
            alias_map[old_symbol] = new_symbol

    def canonical(symbol: str) -> str:
        seen = set()
        while symbol in alias_map:
            if symbol in seen:
                raise ValueError("cycle in symbol alias configuration")
            seen.add(symbol)
            symbol = alias_map[symbol]
        return symbol

    latest_events = {}
    future_events = []
    unknown_events = []
    for event in events:
        raw_date = event.get("effective_date")
        if not raw_date:
            unknown_events.append(event)
            continue
        effective = date.fromisoformat(raw_date)
        symbol = canonical(event["symbol"])
        if event["symbol"] in alias_map:
            alias_record = next(
                item for item in aliases
                if item["old_symbol"] == event["symbol"]
                and date.fromisoformat(item["effective_date"]) <= as_of_date
            )
            if alias_record not in aliases_applied:
                aliases_applied.append(alias_record)
        if effective > as_of_date:
            future_events.append(event)
            continue
        previous = latest_events.get(symbol)
        if previous is None or effective > previous["effective_date"]:
            latest_events[symbol] = {
                "action": event["action"],
                "effective_date": effective,
                "source_symbol": event["symbol"],
            }
        elif effective == previous["effective_date"] and event["action"] != previous["action"]:
            raise ValueError(f"conflicting tracker actions for {symbol} on {effective}")

    current_canonical = {canonical(symbol) for symbol in current_symbols}
    mismatches = []
    for symbol, event in sorted(latest_events.items()):
        expected_present = event["action"] == "introduction"
        actual_present = symbol in current_canonical
        if expected_present != actual_present:
            mismatches.append({
                "symbol": symbol,
                "latest_tracker_action": event["action"],
                "effective_date": event["effective_date"].isoformat(),
                "present_in_current_nse_list": actual_present,
                "source_symbol": event["source_symbol"],
            })

    checked_symbols = set(latest_events)
    status = (
        "incomplete" if unknown_events
        else "mismatch" if mismatches
        else "pass"
    )
    return {
        "status": status,
        "as_of_date_ist": as_of_date.isoformat(),
        "current_individual_stock_count": len(current_symbols),
        "tracker_symbols_checked": len(checked_symbols),
        "baseline_symbols_without_tracker_events": len(current_canonical - checked_symbols),
        "latest_introduction_count": sum(e["action"] == "introduction" for e in latest_events.values()),
        "latest_exclusion_count": sum(e["action"] == "exclusion" for e in latest_events.values()),
        "future_event_count": len(future_events),
        "unknown_effective_date_event_count": len(unknown_events),
        "aliases_applied": aliases_applied,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def normalized_csv(indexes: list[dict], stocks: list[dict]) -> bytes:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(indexes)
    writer.writerows(stocks)
    return stream.getvalue().encode("utf-8")


def ingest(root: Path = Path(".")) -> dict:
    page = _get(SOURCE_PAGE, "https://www.nseindia.com/", "text/html,*/*")
    page_text = page.decode("utf-8", errors="replace")
    if "/api/underlying-information" not in page_text:
        raise ValueError(
            "NSE underlyings page no longer references /api/underlying-information"
        )
    raw = _get(API_URL, SOURCE_PAGE, "application/json,*/*")
    indexes, stocks = parse_underlying_payload(raw)
    normalized = normalized_csv(indexes, stocks)
    raw_hash = hashlib.sha256(raw).hexdigest()
    normalized_hash = hashlib.sha256(normalized).hexdigest()
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    raw_dir = root / "data/raw/fno_underlyings"
    snapshot_dir = root / "data/snapshots/fno_underlyings"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"fno_underlyings_source_{raw_hash[:16]}.json"
    current_path = raw_dir / "fno_underlyings_current.csv"
    snapshot_path = snapshot_dir / f"fno_underlyings_{normalized_hash[:16]}.csv"
    manifest_path = snapshot_dir / f"fno_underlyings_{raw_hash[:16]}_{normalized_hash[:16]}.json"

    for path, expected_hash in ((raw_path, raw_hash), (snapshot_path, normalized_hash)):
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError(f"immutable content-addressed path collision: {path}")
    if not raw_path.exists():
        raw_path.write_bytes(raw)
    if not snapshot_path.exists():
        snapshot_path.write_bytes(normalized)
    if not manifest_path.exists():
        manifest = {
            "dataset": "nse_individual_stock_fno_underlyings",
            "publisher": "National Stock Exchange of India",
            "source_page": SOURCE_PAGE,
            "source_api": API_URL,
            "source_effective_date": None,
            "source_date_semantics": (
                "API supplies no explicit effective date; retrieval timestamp is a "
                "point-in-time observation, not an effective-date claim"
            ),
            "retrieved_at_utc": retrieved_at,
            "index_underlying_count": len(indexes),
            "individual_stock_underlying_count": len(stocks),
            "raw_file": str(raw_path),
            "normalized_file": str(snapshot_path),
            "raw_sha256": raw_hash,
            "normalized_sha256": normalized_hash,
            "symbols_unique": True,
            "serial_numbers_contiguous": True,
            "schema_version": 1,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    current_path.write_bytes(normalized)

    as_of_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    tracker_path = root / "data/raw/fno_tracker/fno_tracker_current.xlsx"
    aliases_path = root / "config/symbol_aliases.json"
    if tracker_path.is_file():
        tracker_bytes = tracker_path.read_bytes()
        tracker_history = parse_tracker_events(tracker_bytes, as_of_date)
        alias_data = json.loads(aliases_path.read_text(encoding="utf-8"))
        reconciliation = reconcile_tracker_events(
            tracker_history["events"],
            {row["symbol"] for row in stocks},
            alias_data.get("aliases", []),
            as_of_date,
        )
        reconciliation["tracker_raw_sha256"] = hashlib.sha256(tracker_bytes).hexdigest()
        reconciliation["symbol_aliases_sha256"] = hashlib.sha256(aliases_path.read_bytes()).hexdigest()
    else:
        reconciliation = {
            "status": "pending_tracker_archive",
            "as_of_date_ist": as_of_date.isoformat(),
            "current_individual_stock_count": len(stocks),
            "note": "Tracker workbook not yet archived; cross-source reconciliation will run after it is committed.",
        }
    reconciliation_bytes = json.dumps(
        reconciliation, indent=2, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    reconciliation_hash = hashlib.sha256(reconciliation_bytes).hexdigest()
    reconciliation_path = snapshot_dir / (
        f"fno_tracker_reconciliation_{as_of_date:%Y%m%d}_{reconciliation_hash[:12]}.json"
    )
    if not reconciliation_path.exists():
        reconciliation_path.write_bytes(reconciliation_bytes + b"\n")
    reconciliation_current_path = raw_dir / "fno_tracker_reconciliation_current.json"
    reconciliation_current_path.write_bytes(reconciliation_bytes + b"\n")
    return {
        "retrieved_at_utc": retrieved_at,
        "index_underlying_count": len(indexes),
        "individual_stock_underlying_count": len(stocks),
        "raw_sha256": raw_hash,
        "normalized_sha256": normalized_hash,
        "raw_path": str(raw_path),
        "current_path": str(current_path),
        "snapshot_path": str(snapshot_path),
        "manifest_path": str(manifest_path),
        "reconciliation": reconciliation,
        "reconciliation_path": str(reconciliation_path),
        "reconciliation_current_path": str(reconciliation_current_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
