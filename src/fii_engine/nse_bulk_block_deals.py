"""Capture official NSE bulk/block deal CSV exports with JSON subset cross-checks."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

BASE = "https://www.nseindia.com"
PAGE = f"{BASE}/report-detail/display-bulk-and-block-deals"
ENDPOINT = f"{BASE}/api/historicalOR/bulk-block-short-deals"
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
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))
PARSER_VERSION = 1
OPTION_TYPES = {"bulk_deals": "bulk", "block_deals": "block"}
CSV_COLUMNS = {
    "Date", "Symbol", "Security Name", "Client Name", "Buy / Sell",
    "Quantity Traded", "Trade Price / Wght. Avg. Price", "Remarks",
}
JSON_FIELDS = {
    "BD_DT_DATE", "BD_SYMBOL", "BD_SCRIP_NAME", "BD_CLIENT_NAME",
    "BD_BUY_SELL", "BD_QTY_TRD", "BD_TP_WATP", "BD_REMARKS",
}
OUTPUT_FIELDS = [
    "trade_date", "deal_type", "symbol", "security_name", "client_name",
    "side", "quantity", "weighted_average_price", "remarks",
    "record_fingerprint",
]


def _get(url: str, referer: str, accept: str, timeout: int = 45) -> tuple[bytes, dict]:
    request = Request(url, headers={**HEADERS, "Referer": referer, "Accept": accept})
    for attempt in range(4):
        try:
            with HTTP.open(request, timeout=timeout) as response:
                payload = response.read()
                return payload, {
                    "http_status": response.status,
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_disposition": response.headers.get("Content-Disposition", ""),
                    "response_url": response.geturl(),
                }
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE deals request failed: {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE deals request failed: {url}: {exc}") from exc
    raise RuntimeError(f"NSE deals request exhausted retries: {url}")


def _parse_query_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d-%m-%Y").date()
    except ValueError as exc:
        raise ValueError(f"invalid deal query date {value!r}; expected DD-MM-YYYY") from exc


def _decimal(value: object, field: str) -> Decimal:
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing NSE deal field {field}")
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"non-numeric NSE deal field {field}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite NSE deal field {field}")
    return parsed


def _normalize_row(
    trade_date_raw: object,
    symbol: object,
    security_name: object,
    client_name: object,
    side: object,
    quantity: object,
    price: object,
    remarks: object,
    deal_type: str,
    from_date: date,
    to_date: date,
) -> dict:
    try:
        trade_date = datetime.strptime(str(trade_date_raw).strip(), "%d-%b-%Y").date()
    except ValueError as exc:
        raise ValueError(f"invalid NSE deal trade date: {trade_date_raw!r}") from exc
    if not from_date <= trade_date <= to_date:
        raise ValueError(
            f"NSE deal date {trade_date} outside requested range {from_date}..{to_date}"
        )
    symbol_text = str(symbol or "").strip()
    security_text = str(security_name or "").strip()
    client_text = str(client_name or "").strip()
    side_text = str(side or "").strip().upper()
    if not symbol_text or not security_text or not client_text:
        raise ValueError("NSE deal row missing symbol, security name or client name")
    if side_text not in {"BUY", "SELL"}:
        raise ValueError(f"invalid NSE deal side: {side_text!r}")
    quantity_value = _decimal(quantity, "Quantity Traded")
    if quantity_value <= 0 or quantity_value != quantity_value.to_integral_value():
        raise ValueError(f"NSE deal quantity must be a positive integer: {quantity!r}")
    price_value = _decimal(price, "Trade Price / Wght. Avg. Price")
    if price_value <= 0:
        raise ValueError(f"NSE deal weighted average price must be positive: {price!r}")
    remarks_text = "" if remarks is None else str(remarks).strip()
    row = {
        "trade_date": trade_date.isoformat(),
        "deal_type": deal_type,
        "symbol": symbol_text,
        "security_name": security_text,
        "client_name": client_text,
        "side": side_text,
        "quantity": str(int(quantity_value)),
        "weighted_average_price": format(price_value.quantize(Decimal("0.01")), ".2f"),
        "remarks": remarks_text,
    }
    fingerprint_payload = json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
    row["record_fingerprint"] = hashlib.sha256(fingerprint_payload).hexdigest()
    return row


def _row_key(row: dict) -> tuple:
    return (
        row["trade_date"], row["symbol"], row["security_name"], row["client_name"],
        row["side"], row["quantity"], row["weighted_average_price"], row["remarks"],
    )


def _parse_csv(
    payload: bytes, deal_type: str, from_date: date, to_date: date
) -> dict:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("NSE deals CSV is not UTF-8") from exc
    if not text.strip():
        raise ValueError("NSE deals CSV body is empty")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError("NSE deals CSV is missing its header")
    headers = [str(x or "").strip() for x in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise ValueError("NSE deals CSV has duplicate headers")
    missing = sorted(CSV_COLUMNS - set(headers))
    if missing:
        raise ValueError(f"NSE deals CSV schema drift; missing columns: {missing}")
    normalized = []
    for line, source_row in enumerate(reader, start=2):
        if None in source_row:
            raise ValueError(f"NSE deals CSV has excess fields at line {line}")
        row = {str(k).strip(): v for k, v in source_row.items()}
        normalized.append(_normalize_row(
            row["Date"], row["Symbol"], row["Security Name"], row["Client Name"],
            row["Buy / Sell"], row["Quantity Traded"],
            row["Trade Price / Wght. Avg. Price"], row["Remarks"],
            deal_type, from_date, to_date,
        ))
    keys = Counter(_row_key(row) for row in normalized)
    return {
        "rows": normalized,
        "source_columns": headers,
        "row_count": len(normalized),
        "distinct_symbols": len({row["symbol"] for row in normalized}),
        "distinct_clients": len({row["client_name"] for row in normalized}),
        "date_counts": dict(sorted(Counter(row["trade_date"] for row in normalized).items())),
        "side_counts": dict(sorted(Counter(row["side"] for row in normalized).items())),
        "exact_duplicate_row_count": sum(count - 1 for count in keys.values() if count > 1),
        "schema_sha256": hashlib.sha256(",".join(headers).encode()).hexdigest(),
    }


def _parse_json(
    payload: bytes, deal_type: str, from_date: date, to_date: date
) -> dict:
    try:
        decoded = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE deals JSON response is not valid JSON") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), list):
        raise ValueError("NSE deals JSON schema drift: expected object with data list")
    rows = []
    for index, source_row in enumerate(decoded["data"]):
        if not isinstance(source_row, dict):
            raise ValueError(f"NSE deals JSON row {index} is not an object")
        missing = sorted(JSON_FIELDS - set(source_row))
        if missing:
            raise ValueError(f"NSE deals JSON row {index} missing fields: {missing}")
        rows.append(_normalize_row(
            source_row["BD_DT_DATE"], source_row["BD_SYMBOL"],
            source_row["BD_SCRIP_NAME"], source_row["BD_CLIENT_NAME"],
            source_row["BD_BUY_SELL"], source_row["BD_QTY_TRD"],
            source_row["BD_TP_WATP"], source_row["BD_REMARKS"],
            deal_type, from_date, to_date,
        ))
    return {"rows": rows, "row_count": len(rows)}



def _crosscheck_json_csv(json_rows: list[dict], csv_rows: list[dict]) -> dict:
    json_keys = Counter(_row_key(row) for row in json_rows)
    csv_keys = Counter(_row_key(row) for row in csv_rows)
    unmatched = json_keys - csv_keys
    if unmatched:
        raise ValueError(
            f"NSE JSON contains {sum(unmatched.values())} records absent from CSV export"
        )
    return {
        "json_is_subset_of_csv": True,
        "json_complete_for_range": json_keys == csv_keys,
        "json_row_count": len(json_rows),
        "csv_row_count": len(csv_rows),
        "json_coverage_ratio": (
            round(len(json_rows) / len(csv_rows), 6) if csv_rows else 1.0
        ),
    }

def _write_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _save_raw(root: Path, deal_type: str, from_date: date, to_date: date,
              fmt: str, payload: bytes) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    folder = root / "data/raw/bulk_block_deals"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (
        f"{deal_type}_{from_date:%Y%m%d}_{to_date:%Y%m%d}_{fmt}_{digest[:16]}."
        f"{'csv' if fmt == 'csv' else 'json'}"
    )
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"immutable NSE deals raw hash mismatch: {path}")
    else:
        path.write_bytes(payload)
    return {"path": str(path), "sha256": digest, "bytes": len(payload)}


def _persist_date_snapshots(
    root: Path, deal_type: str, grouped: dict[str, list[dict]],
    csv_raw: dict, json_raw: dict, csv_summary: dict, json_summary: dict,
    retrieved_at: str, from_date: date, to_date: date,
) -> list[dict]:
    snapshot_dir = root / "data/snapshots/bulk_block_deals"
    manifest_dir = root / "data/raw/bulk_block_deals/manifests"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    results = []
    crosscheck = _crosscheck_json_csv(json_summary["rows"], csv_summary["rows"])
    json_complete = crosscheck["json_complete_for_range"]
    for trade_date, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda row: (
            row["symbol"], row["client_name"], row["side"],
            row["quantity"], row["weighted_average_price"], row["record_fingerprint"],
        ))
        snapshot_payload = _write_csv(rows)
        snapshot_hash = hashlib.sha256(snapshot_payload).hexdigest()
        snapshot_path = snapshot_dir / (
            f"{deal_type}_{trade_date}_{snapshot_hash[:16]}_p{PARSER_VERSION}.csv"
        )
        if snapshot_path.exists():
            if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != snapshot_hash:
                raise RuntimeError(f"immutable NSE deals snapshot hash mismatch: {snapshot_path}")
        else:
            snapshot_path.write_bytes(snapshot_payload)
        date_json_rows = [row for row in json_summary["rows"] if row["trade_date"] == trade_date]
        manifest = {
            "dataset": f"nse_{deal_type}_deals",
            "publisher": "National Stock Exchange of India",
            "reference_url": PAGE,
            "api_url": ENDPOINT,
            "deal_type": deal_type,
            "trade_date": trade_date,
            "requested_from": from_date.isoformat(),
            "requested_to": to_date.isoformat(),
            "retrieved_at_utc": retrieved_at,
            "finality": "exchange_disclosed_historical_transaction_data",
            "raw_csv_path": csv_raw["path"],
            "raw_csv_sha256": csv_raw["sha256"],
            "raw_json_path": json_raw["path"],
            "raw_json_sha256": json_raw["sha256"],
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": snapshot_hash,
            "row_count": len(rows),
            "json_row_count_for_date": len(date_json_rows),
            "json_complete_for_range": json_complete,
            "json_is_subset_of_csv": True,
            "csv_range_row_count": csv_summary["row_count"],
            "json_range_row_count": json_summary["row_count"],
            "json_coverage_ratio": (
                round(json_summary["row_count"] / csv_summary["row_count"], 6)
                if csv_summary["row_count"] else 1.0
            ),
            "source_csv_schema_sha256": csv_summary["schema_sha256"],
            "parser_version": PARSER_VERSION,
            "attribution_boundary": (
                "client name is an exchange disclosure, not an FII/FPI or "
                "beneficial-owner classification"
            ),
            "date_semantics": "use explicit CSV Date field; ignore BD_DT_ORDER UTC timestamp",
        }
        manifest_hash = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest()
        manifest_path = manifest_dir / (
            f"{deal_type}_{trade_date}_{manifest_hash[:16]}.json"
        )
        if not manifest_path.exists():
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        results.append({
            "trade_date": trade_date,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": snapshot_hash,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_hash,
            "row_count": len(rows),
        })
    return results


def _capture_range(root: Path, option_type: str, from_date: date, to_date: date,
                   retrieved_at: str) -> dict:
    deal_type = OPTION_TYPES[option_type]
    query = {
        "optionType": option_type,
        "from": from_date.strftime("%d-%m-%Y"),
        "to": to_date.strftime("%d-%m-%Y"),
    }
    json_url = ENDPOINT + "?" + urlencode(query)
    csv_url = ENDPOINT + "?" + urlencode({**query, "csv": "true"})
    json_payload, json_meta = _get(json_url, PAGE, "application/json,text/plain,*/*")
    csv_payload, csv_meta = _get(csv_url, PAGE, "text/csv,application/octet-stream,*/*")
    if json_meta["http_status"] != 200 or csv_meta["http_status"] != 200:
        raise RuntimeError(f"NSE {deal_type} endpoint did not return HTTP 200")
    if "text/csv" not in csv_meta["content_type"].lower():
        raise ValueError(
            f"NSE {deal_type} CSV endpoint returned unexpected content type: "
            f"{csv_meta['content_type']}"
        )
    csv_raw = _save_raw(root, deal_type, from_date, to_date, "csv", csv_payload)
    json_raw = _save_raw(root, deal_type, from_date, to_date, "json", json_payload)
    csv_summary = _parse_csv(csv_payload, deal_type, from_date, to_date)
    json_summary = _parse_json(json_payload, deal_type, from_date, to_date)
    crosscheck = _crosscheck_json_csv(json_summary["rows"], csv_summary["rows"])
    grouped: dict[str, list[dict]] = {}
    for row in csv_summary["rows"]:
        grouped.setdefault(row["trade_date"], []).append(row)
    snapshots = _persist_date_snapshots(
        root, deal_type, grouped, csv_raw, json_raw,
        csv_summary, json_summary, retrieved_at, from_date, to_date,
    )
    return {
        "option_type": option_type,
        "deal_type": deal_type,
        "request_from": query["from"],
        "request_to": query["to"],
        "json_request_url": json_url,
        "csv_request_url": csv_url,
        "json_http": json_meta,
        "csv_http": csv_meta,
        "raw_json": json_raw,
        "raw_csv": csv_raw,
        "json_row_count": json_summary["row_count"],
        "csv_row_count": csv_summary["row_count"],
        **crosscheck,
        "csv_validation": {
            k: v for k, v in csv_summary.items() if k != "rows"
        },
        "snapshots": snapshots,
    }



def _default_query_range(today: date) -> tuple[date, date]:
    # The NSE page's one-week default ends on the previous weekday when
    # inspected over a weekend. This keeps Friday's transactions in a
    # Saturday/Sunday manual run instead of shifting the window forward.
    if today.weekday() == 5:
        to_date = today - timedelta(days=1)
    elif today.weekday() == 6:
        to_date = today - timedelta(days=2)
    else:
        to_date = today
    return to_date - timedelta(days=7), to_date

def ingest(root: Path = Path("."), from_value: str | None = None,
           to_value: str | None = None) -> dict:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    default_from, default_to = _default_query_range(today)
    to_date = _parse_query_date(to_value) if to_value else default_to
    from_date = _parse_query_date(from_value) if from_value else default_from
    if from_date > to_date:
        raise ValueError("NSE deals from-date must not be after to-date")
    if (to_date - from_date).days > 365:
        raise ValueError("NSE deals date range must not exceed 365 days")

    # Bootstrap with the actual report page; NSE may reject the generic homepage
    # from hosted runners even while this report page is available.
    bootstrap, bootstrap_meta = _get(PAGE, BASE + "/", "text/html,*/*")
    if not bootstrap or bootstrap_meta["http_status"] != 200:
        raise RuntimeError("NSE bulk/block deals report-page session bootstrap failed")
    retrieved_at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    results = [
        _capture_range(root, option_type, from_date, to_date, retrieved_at)
        for option_type in OPTION_TYPES
    ]
    current = {
        "publisher": "National Stock Exchange of India",
        "source_page": PAGE,
        "api_url": ENDPOINT,
        "retrieved_at_utc": retrieved_at,
        "requested_from": from_date.isoformat(),
        "requested_to": to_date.isoformat(),
        "session_bootstrap_http_status": bootstrap_meta["http_status"],
        "datasets": results,
        "data_boundaries": [
            "CSV export is the primary full-range dataset; JSON page data is cross-checked as a subset.",
            "A JSON response smaller than CSV is not treated as a complete range.",
            "Client names are not automatically classified as FII/FPI or beneficial owners.",
            "Only the explicit deal Date field is used; BD_DT_ORDER UTC timestamps are not substituted.",
        ],
    }
    current_path = root / "data/raw/bulk_block_deals/current_manifest.json"
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--from-date", type=str, default=None, help="DD-MM-YYYY")
    parser.add_argument("--to-date", type=str, default=None, help="DD-MM-YYYY")
    args = parser.parse_args()
    print(json.dumps(
        ingest(args.repo_root, args.from_date, args.to_date),
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
