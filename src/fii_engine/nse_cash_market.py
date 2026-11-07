"""Capture official NSE cash-market UDiFF prices and security delivery data."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import time
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://www.nseindia.com"
PAGE = f"{BASE}/all-reports"
API = f"{BASE}/api/daily-reports?key=CM"
ARCHIVE_HOST = "nsearchives.nseindia.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Cache-Control": "no-cache", "Pragma": "no-cache"}
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))
PARSER_VERSION = 1
UDIFF_RE = re.compile(r"^BhavCopy_NSE_CM_0_0_0_(\d{8})_F_0000\.csv\.zip$")
DELIVERY_RE = re.compile(r"^sec_bhavdata_full_(\d{8})\.csv$")
UDIFF_REQUIRED = {
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId",
    "ISIN", "TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric",
    "ClsPric", "LastPric", "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd",
}
DELIVERY_REQUIRED = {
    "SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE",
    "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE", "AVG_PRICE",
    "TTL_TRD_QNTY", "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER",
}
PRICE_NUMERIC = {
    "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric",
    "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd",
}
DELIVERY_NUMERIC = {
    "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE",
    "CLOSE_PRICE", "AVG_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS",
    "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER",
}


def _get(url: str, referer: str, accept: str, timeout: int = 60) -> bytes:
    req = Request(url, headers={**HEADERS, "Referer": referer, "Accept": accept})
    for attempt in range(4):
        try:
            with HTTP.open(req, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {url}")
                return response.read()
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE request failed: {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE request failed: {url}: {exc}") from exc
    raise RuntimeError(f"NSE request exhausted retries: {url}")


def fetch_catalogue() -> dict:
    _get(PAGE, BASE + "/", "text/html,*/*")
    raw = _get(API, PAGE, "application/json,*/*")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE CM catalogue did not return valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("currentDate"), str):
        raise ValueError("NSE CM catalogue schema drift: currentDate missing")
    if not all(isinstance(data.get(k), list) for k in ("CurrentDay", "PreviousDay")):
        raise ValueError("NSE CM catalogue schema drift: report lists missing")
    return data


def _trade_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d-%b-%Y").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid NSE catalogue trading date: {value!r}") from exc


def _select(catalogue: dict, kind: str) -> dict:
    candidates = []
    for group in ("CurrentDay", "PreviousDay"):
        for item in catalogue[group]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("displayName", "")).casefold()
            filename = item.get("fileActlName")
            if kind == "udiff" and "udiff common bhavcopy final" in name:
                pattern = UDIFF_RE
            elif kind == "delivery" and "full bhavcopy and security deliverable data" in name:
                pattern = DELIVERY_RE
            else:
                continue
            if not isinstance(filename, str) or not pattern.fullmatch(filename):
                continue
            parsed = urlparse(item.get("filePath", ""))
            if (parsed.scheme != "https" or parsed.hostname != ARCHIVE_HOST
                    or parsed.query or parsed.fragment):
                raise ValueError(f"untrusted NSE {kind} archive directory")
            date_match = pattern.fullmatch(filename)
            token = date_match.group(1)
            filename_date = (datetime.strptime(token, "%Y%m%d").date()
                             if kind == "udiff" else datetime.strptime(token, "%d%m%Y").date())
            trading_date = _trade_date(item.get("tradingDate"))
            if filename_date != trading_date:
                raise ValueError(f"NSE {kind} catalogue/file date mismatch")
            candidates.append({
                "kind": kind, "display_name": item["displayName"],
                "report_key": item.get("fileKey"), "filename": filename,
                "trading_date": trading_date.isoformat(),
                "catalogue_group": group,
                "download_url": item["filePath"].rstrip("/") + "/" + filename,
                "reported_file_size": item.get("fileSize"),
                "file_hash_key": item.get("fileHashKey"),
            })
    if not candidates:
        raise ValueError(f"NSE CM catalogue has no valid {kind} report")
    candidates.sort(key=lambda x: x["trading_date"])
    return candidates[-1]


def _csv(payload: bytes, dataset: str) -> tuple[list[str], list[dict]]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{dataset}: invalid UTF-8 CSV") from exc
    if not reader.fieldnames:
        raise ValueError(f"{dataset}: missing CSV header")
    headers = [x.strip() for x in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise ValueError(f"{dataset}: duplicate CSV headers")
    rows = []
    for line, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"{dataset}: excess fields at CSV line {line}")
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    if not rows:
        raise ValueError(f"{dataset}: no data rows")
    return headers, rows


def _number(value: str, field: str, line: int) -> Decimal | None:
    if value in {"", "-"}:
        return None
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"non-numeric {field} at line {line}: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite {field} at line {line}")
    return number


def validate_udiff(payload: bytes, trade_date: str) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"CM UDiFF ZIP CRC failure: {bad}")
            members = [x for x in archive.infolist() if not x.is_dir()]
            if len(members) != 1 or not members[0].filename.lower().endswith(".csv"):
                raise ValueError("CM UDiFF ZIP must contain exactly one CSV")
            if members[0].file_size <= 0 or members[0].file_size > 250_000_000:
                raise ValueError("CM UDiFF expanded file size outside safety bounds")
            csv_bytes = archive.read(members[0])
            member_name = members[0].filename
    except zipfile.BadZipFile as exc:
        raise ValueError("NSE CM UDiFF download is not a ZIP archive") from exc
    headers, rows = _csv(csv_bytes, "CM UDiFF")
    missing = sorted(UDIFF_REQUIRED - set(headers))
    if missing:
        raise ValueError(f"CM UDiFF schema drift; missing columns: {missing}")
    types, series = {}, {}
    for line, row in enumerate(rows, start=2):
        if row.get("TradDt") != trade_date:
            raise ValueError(f"CM UDiFF TradDt mismatch at line {line}")
        if row.get("Sgmt") != "CM":
            raise ValueError(f"unexpected CM UDiFF segment at line {line}: {row.get('Sgmt')}")
        if not row.get("TckrSymb") or not row.get("SctySrs"):
            raise ValueError(f"CM UDiFF missing symbol/series at line {line}")
        for field in PRICE_NUMERIC:
            _number(row.get(field, ""), field, line)
        kind = row.get("FinInstrmTp", "")
        types[kind] = types.get(kind, 0) + 1
        key = (row["FinInstrmId"], row["TckrSymb"], row["SctySrs"])
        # Same instrument ID should not duplicate within the final cash file.
        if key in series:
            raise ValueError(f"duplicate CM UDiFF instrument key at line {line}: {key}")
        series[key] = True
    return {
        "csv_bytes": csv_bytes, "member_name": member_name,
        "row_count": len(rows), "column_count": len(headers),
        "columns": headers, "schema_sha256": hashlib.sha256(
            ",".join(headers).encode()
        ).hexdigest(),
        "instrument_type_counts": dict(sorted(types.items())),
        "distinct_symbols": len({r["TckrSymb"] for r in rows}),
        "series_counts": {s: sum(1 for r in rows if r["SctySrs"] == s)
                          for s in sorted({r["SctySrs"] for r in rows})},
    }


def validate_delivery(payload: bytes, trade_date: str) -> dict:
    headers, rows = _csv(payload, "Full Bhavcopy and Security Deliverable data")
    missing = sorted(DELIVERY_REQUIRED - set(headers))
    if missing:
        raise ValueError(f"delivery report schema drift; missing columns: {missing}")
    parsed_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    seen = set()
    symbols = set()
    observation_counts = {
        "reported": 0,
        "no_trades_delivery_not_reported": 0,
        "not_reported": 0,
    }
    for line, row in enumerate(rows, start=2):
        try:
            row_date = datetime.strptime(row["DATE1"], "%d-%b-%Y").date()
        except ValueError as exc:
            raise ValueError(f"invalid delivery DATE1 at line {line}") from exc
        if row_date != parsed_date:
            raise ValueError(f"delivery report date mismatch at line {line}")
        if not row["SYMBOL"] or not row["SERIES"]:
            raise ValueError(f"delivery report missing symbol/series at line {line}")
        key = (row["SYMBOL"], row["SERIES"], row["DATE1"])
        if key in seen:
            raise ValueError(f"duplicate delivery security key at line {line}: {key}")
        seen.add(key)
        symbols.add(row["SYMBOL"])
        values = {field: _number(row[field], field, line) for field in DELIVERY_NUMERIC}
        traded = values["TTL_TRD_QNTY"]
        delivered = values["DELIV_QTY"]
        pct = values["DELIV_PER"]
        price_fields = ("OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE")

        if traded is None and delivered is None and pct is None:
            if any(values[field] is not None for field in price_fields):
                raise ValueError(f"delivery row has prices but no quantity observations at line {line}")
            observation_counts["not_reported"] += 1
            continue
        if traded == Decimal(0) and delivered is None and pct is None:
            observation_counts["no_trades_delivery_not_reported"] += 1
            continue
        if traded is None or delivered is None or pct is None:
            raise ValueError(f"incomplete delivery quantity/percentage fields at line {line}")
        if traded < 0 or delivered < 0 or delivered > traded:
            raise ValueError(f"invalid traded/delivered quantity at line {line}")
        expected = (delivered / traded * 100) if traded else Decimal(0)
        if abs(expected - pct) > Decimal("0.11"):
            raise ValueError(f"delivery percentage mismatch at line {line}")
        if any(values[field] is None for field in price_fields):
            raise ValueError(f"missing OHLC price on traded security at line {line}")
        observation_counts["reported"] += 1
    return {
        "row_count": len(rows), "column_count": len(headers),
        "columns": headers, "schema_sha256": hashlib.sha256(
            ",".join(headers).encode()
        ).hexdigest(),
        "distinct_symbols": len(symbols),
        "series_counts": {s: sum(1 for r in rows if r["SERIES"] == s)
                          for s in sorted({r["SERIES"] for r in rows})},
        "delivery_observation_counts": observation_counts,
    }

def _capture(record: dict) -> bytes:
    payload = _get(record["download_url"], PAGE, "application/zip,text/csv,application/octet-stream,*/*")
    if not payload:
        raise ValueError(f"NSE {record['kind']} source returned an empty file")
    # NSE catalogue fileSize is human-formatted (for example, "203.19 KB"),
    # not a byte integer. Preserve it verbatim; actual byte count is recorded
    # independently in the source manifest. Never parse rounded display text as bytes.
    return payload


def _save(root: Path, kind: str, record: dict, payload: bytes, parsed: dict,
          csv_payload: bytes | None, retrieved_at: str) -> dict:
    raw_hash = hashlib.sha256(payload).hexdigest()
    raw_dir = root / "data/raw/cash_market"
    parsed_dir = root / "data/processed/cash_market"
    manifests = raw_dir / "manifests"
    for folder in (raw_dir, parsed_dir, manifests):
        folder.mkdir(parents=True, exist_ok=True)
    suffix = ".zip" if kind == "udiff" else ".csv"
    raw_path = raw_dir / f"{kind}_{record['trading_date']}_{raw_hash[:16]}{suffix}"
    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_hash:
        raise RuntimeError(f"immutable raw hash mismatch: {raw_path}")
    if not raw_path.exists():
        raw_path.write_bytes(payload)
    csv_path = None
    csv_hash = None
    if csv_payload is not None:
        csv_hash = hashlib.sha256(csv_payload).hexdigest()
        csv_path = parsed_dir / f"{kind}_{record['trading_date']}_{csv_hash[:16]}_p{PARSER_VERSION}.csv"
        if csv_path.exists() and hashlib.sha256(csv_path.read_bytes()).hexdigest() != csv_hash:
            raise RuntimeError(f"immutable parsed hash mismatch: {csv_path}")
        if not csv_path.exists():
            csv_path.write_bytes(csv_payload)
    manifest = {
        "dataset": "nse_cm_udiff_prices" if kind == "udiff" else "nse_cm_security_delivery",
        "publisher": "National Stock Exchange of India",
        "reference_url": PAGE, "catalogue_api": API,
        "report": record, "retrieved_at_utc": retrieved_at,
        "finality": "final", "trade_date": record["trading_date"],
        "raw_path": str(raw_path), "raw_sha256": raw_hash,
        "raw_bytes": len(payload), "parsed_path": str(csv_path) if csv_path else None,
        "parsed_sha256": csv_hash, "parser_version": PARSER_VERSION,
        "validation": {k: v for k, v in parsed.items() if k not in {"csv_bytes"}},
        "semantic_scope": (
            "market-wide cash-market security price/volume; not participant attribution"
            if kind == "udiff" else
            "security-wise traded and deliverable quantities; not beneficial ownership"
        ),
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    manifest_path = manifests / f"{kind}_{record['trading_date']}_{manifest_hash[:16]}.json"
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"raw_path": str(raw_path), "raw_sha256": raw_hash,
            "parsed_path": str(csv_path) if csv_path else None,
            "parsed_sha256": csv_hash, "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_hash, "trade_date": record["trading_date"],
            "validation": manifest["validation"]}


def ingest(root: Path = Path(".")) -> dict:
    catalogue = fetch_catalogue()
    udiff_record = _select(catalogue, "udiff")
    delivery_record = _select(catalogue, "delivery")
    if udiff_record["trading_date"] != delivery_record["trading_date"]:
        raise ValueError(
            "NSE CM UDiFF and delivery report dates differ: "
            f"{udiff_record['trading_date']} vs {delivery_record['trading_date']}"
        )
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    udiff_raw = _capture(udiff_record)
    udiff = validate_udiff(udiff_raw, udiff_record["trading_date"])
    delivery_raw = _capture(delivery_record)
    delivery = validate_delivery(delivery_raw, udiff_record["trading_date"])
    results = [
        _save(root, "udiff", udiff_record, udiff_raw, udiff,
              udiff["csv_bytes"], retrieved),
        _save(root, "delivery", delivery_record, delivery_raw, delivery,
              None, retrieved),
    ]
    current = {
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": retrieved,
        "catalogue_current_date": catalogue["currentDate"],
        "catalogue_previous_date": catalogue.get("previousDate"),
        "trade_date": udiff_record["trading_date"],
        "finality": "final",
        "datasets": results,
        "data_boundaries": [
            "CM UDiFF price/volume is market-wide, not participant-specific.",
            "Delivery quantity is exchange-reported deliverable quantity, not FII ownership.",
            "Delivery percentage is not a standalone accumulation signal.",
        ],
    }
    current_path = root / "data/raw/cash_market/nse_cm_cash_current_manifest.json"
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
