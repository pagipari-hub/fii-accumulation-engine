"""Capture and validate official NSE-only FII/FPI and DII cash-market flows."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_URL = "https://www.nseindia.com"
REPORT_PAGE = f"{BASE_URL}/reports/fii-dii"
API_URL = f"{BASE_URL}/api/fiidiiTradeReact"
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
REQUIRED_FIELDS = {"date", "category", "buyValue", "sellValue", "netValue"}
EXPECTED_CATEGORIES = {"FII/FPI", "DII"}
PARSER_VERSION = 1


def _get(url: str, referer: str, accept: str, timeout: int = 45) -> bytes:
    request = Request(
        url,
        headers={**HEADERS, "Accept": accept, "Referer": referer},
    )
    for attempt in range(4):
        try:
            with HTTP.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP {response.status} for {url}")
                return response.read()
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE request failed for {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE request failed for {url}: {exc}") from exc
    raise RuntimeError(f"NSE request failed after retries for {url}")


def fetch_raw() -> bytes:
    # Establish the cookie session from the exact official FII/DII report page.
    _get(REPORT_PAGE, BASE_URL + "/", "text/html,*/*")
    payload = _get(API_URL, REPORT_PAGE, "application/json,text/plain,*/*")
    if not payload:
        raise ValueError("NSE FII/DII API returned an empty response")
    return payload


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"invalid NSE FII/DII date: {value!r}") from exc


def _decimal(value: object, field: str, category: str, trade_date: str) -> Decimal:
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing {field} for {category} on {trade_date}")
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(
            f"non-numeric {field} for {category} on {trade_date}: {value!r}"
        ) from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite {field} for {category} on {trade_date}")
    return parsed.quantize(Decimal("0.01"))


def parse_rows(payload: bytes) -> dict:
    try:
        raw = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE FII/DII API did not return valid JSON") from exc
    if not isinstance(raw, list) or not raw:
        raise ValueError("NSE FII/DII API response must be a non-empty JSON list")

    normalized = []
    seen = set()
    raw_fields = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"NSE FII/DII row {index} is not a JSON object")
        raw_fields.update(item)
        missing = sorted(REQUIRED_FIELDS - set(item))
        if missing:
            raise ValueError(f"NSE FII/DII row {index} missing fields: {missing}")
        trade_date = _parse_date(item["date"]).isoformat()
        raw_category = str(item["category"]).strip()
        category = raw_category.rstrip("*").strip()
        if category not in EXPECTED_CATEGORIES:
            raise ValueError(
                f"unknown NSE FII/DII category at row {index}: {raw_category!r}"
            )
        key = (trade_date, category)
        if key in seen:
            raise ValueError(f"duplicate NSE FII/DII row for {trade_date}/{category}")
        seen.add(key)

        buy = _decimal(item["buyValue"], "buyValue", category, trade_date)
        sell = _decimal(item["sellValue"], "sellValue", category, trade_date)
        net = _decimal(item["netValue"], "netValue", category, trade_date)
        arithmetic_difference = net - (buy - sell)
        if abs(arithmetic_difference) > Decimal("0.01"):
            raise ValueError(
                f"NSE FII/DII net arithmetic mismatch for {category} on {trade_date}: "
                f"reported={net}, buy-sell={buy - sell}"
            )
        normalized.append({
            "trade_date": trade_date,
            "category": category,
            "raw_category": raw_category,
            "scope": "NSE_only",
            "segment": "capital_market",
            "currency": "INR",
            "unit": "crore",
            "buy_value_crore": format(buy, ".2f"),
            "sell_value_crore": format(sell, ".2f"),
            "net_value_crore": format(net, ".2f"),
            "net_arithmetic_difference_crore": format(arithmetic_difference, ".2f"),
            "publication_status": (
                "provisional" if category == "FII/FPI" else "exchange_reported"
            ),
        })

    dates = sorted({row["trade_date"] for row in normalized})
    for trade_date in dates:
        categories = {row["category"] for row in normalized if row["trade_date"] == trade_date}
        if categories != EXPECTED_CATEGORIES:
            raise ValueError(
                f"incomplete NSE FII/DII date {trade_date}: "
                f"categories={sorted(categories)}"
            )
    normalized.sort(key=lambda row: (row["trade_date"], row["category"]))
    return {
        "rows": normalized,
        "trade_dates": dates,
        "raw_row_count": len(raw),
        "normalized_row_count": len(normalized),
        "raw_fields": sorted(raw_fields),
        "scope": "NSE_only",
        "segment": "capital_market",
        "source_status": {
            "FII/FPI": "provisional_subject_to_custodial_confirmation_and_modifications",
            "DII": "exchange_reported_from_member_client_category_classification",
        },
        "source_notes": [
            "NSE-only capital-market segment; do not mix with the combined NSE/BSE/MSEI series.",
            "FII/FPI data is provisional and may change after custodial confirmation and modifications.",
            "Expiry-day physical-settlement obligations may be included in FII/FPI data.",
            "Final FPI data must be sourced separately from NSDL/CDSL.",
        ],
    }


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("cannot serialize an empty NSE FII/DII snapshot")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def ingest(root: Path = Path("."), raw_payload: bytes | None = None) -> dict:
    payload = raw_payload if raw_payload is not None else fetch_raw()
    parsed = parse_rows(payload)
    raw_hash = hashlib.sha256(payload).hexdigest()
    retrieved_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )

    raw_dir = root / "data/raw/fii_dii"
    snapshot_dir = root / "data/snapshots/fii_dii"
    manifest_dir = raw_dir / "manifests"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"nse_fii_dii_{raw_hash[:16]}.json"
    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_hash:
        raise RuntimeError("immutable NSE FII/DII raw response hash mismatch")
    if not raw_path.exists():
        raw_path.write_bytes(payload)

    snapshots = []
    for trade_date in parsed["trade_dates"]:
        rows = [row for row in parsed["rows"] if row["trade_date"] == trade_date]
        csv_payload = _csv_bytes(rows)
        csv_hash = hashlib.sha256(csv_payload).hexdigest()
        snapshot_path = snapshot_dir / (
            f"nse_fii_dii_{trade_date}_{csv_hash[:16]}_p{PARSER_VERSION}.csv"
        )
        if snapshot_path.exists():
            if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != csv_hash:
                raise RuntimeError(f"immutable NSE FII/DII snapshot hash mismatch: {snapshot_path}")
        else:
            snapshot_path.write_bytes(csv_payload)
        manifest = {
            "dataset": "nse_fii_dii_cash_activity",
            "publisher": "National Stock Exchange of India",
            "reference_url": REPORT_PAGE,
            "api_url": API_URL,
            "trade_date": trade_date,
            "scope": "NSE_only",
            "segment": "capital_market",
            "retrieved_at_utc": retrieved_at,
            "publication_status": {
                row["category"]: row["publication_status"] for row in rows
            },
            "raw_response_path": str(raw_path),
            "raw_response_sha256": raw_hash,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": csv_hash,
            "row_count": len(rows),
            "parser_version": PARSER_VERSION,
            "source_notes": parsed["source_notes"],
        }
        manifest_path = manifest_dir / (
            f"nse_fii_dii_{trade_date}_{csv_hash[:16]}_p{PARSER_VERSION}.json"
        )
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("snapshot_sha256") != csv_hash:
                raise RuntimeError("immutable NSE FII/DII manifest hash mismatch")
        else:
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
        snapshots.append({
            "trade_date": trade_date,
            "snapshot_path": str(snapshot_path),
            "snapshot_sha256": csv_hash,
            "manifest_path": str(manifest_path),
            "row_count": len(rows),
        })

    current = {
        "dataset": "nse_fii_dii_cash_activity",
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": retrieved_at,
        "raw_response_path": str(raw_path),
        "raw_response_sha256": raw_hash,
        "raw_response_bytes": len(payload),
        "scope": "NSE_only",
        "segment": "capital_market",
        "parsed": parsed,
        "snapshots": snapshots,
    }
    current_path = raw_dir / "current_manifest.json"
    current_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
