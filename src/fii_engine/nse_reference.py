"""Discover and capture NSE's official daily MII security and F&O contract masters.

The collector uses the public report catalogue's own metadata for filenames and paths.
It preserves downloaded compressed source bytes in the workflow artifact and records
hashes/provenance. It does not infer F&O eligibility from the cash security master.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

BASE_URL = "https://www.nseindia.com"
CM_REPORT_PAGE = f"{BASE_URL}/all-reports"
FO_REPORT_PAGE = f"{BASE_URL}/all-reports-derivatives"
REPORT_API = f"{BASE_URL}/api/daily-reports"
ARCHIVE_HOST = "nsearchives.nseindia.com"
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


def _get(url: str, referer: str, accept: str, timeout: int = 45) -> bytes:
    request = Request(
        url,
        headers={**HEADERS, "Accept": accept, "Referer": referer},
    )
    try:
        with HTTP.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} for {url}")
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"NSE request failed for {url}: {exc}") from exc


def _clean_text(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())


def parse_catalog(payload: bytes) -> dict:
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE daily-report API did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("NSE daily-report API schema drift: expected an object")
    current = data.get("CurrentDay")
    if not isinstance(current, list) or not current:
        raise ValueError("NSE daily-report API has no CurrentDay report list")
    current_date = data.get("currentDate")
    if not isinstance(current_date, str) or not current_date.strip():
        raise ValueError("NSE daily-report API omitted currentDate")
    return data


def _validate_report_record(record: dict, dataset: str, current_date: str) -> dict:
    required = ("fileActlName", "filePath", "tradingDate", "displayName")
    if any(not isinstance(record.get(key), str) or not record[key].strip() for key in required):
        raise ValueError(f"{dataset}: report metadata missing required fields")
    if record["tradingDate"] != current_date:
        raise ValueError(
            f"{dataset}: selected report date {record['tradingDate']!r} "
            f"does not match catalogue currentDate {current_date!r}"
        )
    filename = record["fileActlName"].strip()
    if Path(filename).name != filename or not filename.lower().endswith(".csv.gz"):
        raise ValueError(f"{dataset}: unexpected report filename {filename!r}")
    parsed = urlparse(record["filePath"])
    if parsed.scheme != "https" or parsed.hostname != ARCHIVE_HOST or parsed.query or parsed.fragment:
        raise ValueError(f"{dataset}: untrusted archive path {record['filePath']!r}")
    return {
        "dataset": dataset,
        "display_name": _clean_text(record["displayName"]),
        "file_key": record.get("fileKey"),
        "filename": filename,
        "trading_date": record["tradingDate"],
        "download_url": record["filePath"].rstrip("/") + "/" + filename,
        "file_size_reported": record.get("fileSize"),
        "file_position": record.get("filePosition"),
        "file_hash_key": record.get("fileHashKey"),
    }


def select_cm_security_master(catalog: dict) -> dict:
    current_date = catalog["currentDate"]
    matches = []
    for record in catalog["CurrentDay"]:
        if not isinstance(record, dict):
            continue
        label = _clean_text(str(record.get("displayName", ""))).casefold()
        if (
            "mii" in label
            and "security file" in label
            and "nse listed securities" in label
            and "nse and bse" not in label
        ):
            matches.append(record)
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one NSE-listed CM MII security master; found {len(matches)}"
        )
    return _validate_report_record(matches[0], "nse_cm_security_master", current_date)


def select_fo_contract_master(catalog: dict) -> dict:
    current_date = catalog["currentDate"]
    matches = [
        record for record in catalog["CurrentDay"]
        if isinstance(record, dict)
        and str(record.get("fileKey", "")).upper() == "NSE-FO-CONTRACT-CSV"
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one NSE-exclusive FO MII contract master; found {len(matches)}"
        )
    selected = _validate_report_record(matches[0], "nse_fo_contract_master", current_date)
    label = selected["display_name"].casefold()
    if "nse exclusive contract" not in label:
        raise ValueError("FO contract record is not explicitly the NSE-exclusive contract file")
    return selected


def _csv_rows(expanded: bytes, required: set[str], dataset: str) -> list[dict[str, str]]:
    import csv
    import io

    try:
        reader = csv.DictReader(io.StringIO(expanded.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{dataset}: CSV is not UTF-8") from exc
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        missing = sorted(required - set(reader.fieldnames or []))
        raise ValueError(f"{dataset}: schema drift; missing columns {missing}")
    rows = list(reader)
    if not rows:
        raise ValueError(f"{dataset}: CSV contains no data rows")
    return rows


def inspect_cm_quality(expanded: bytes, trading_date: str) -> dict:
    required = {"TckrSymb", "SctySrs", "ISIN", "DelFlg", "UpdDt"}
    rows = _csv_rows(expanded, required, "CM security master")
    updates = [
        int(row["UpdDt"]) for row in rows
        if row.get("UpdDt", "").isdigit() and int(row["UpdDt"]) > 0
    ]
    if not updates:
        raise ValueError("CM security master has no parseable UpdDt values")
    latest_update = datetime.fromtimestamp(max(updates), tz=timezone.utc).date()
    report_date = datetime.strptime(trading_date, "%d-%b-%Y").date()
    test_rows = sum(
        "NSETEST" in row.get("TckrSymb", "").upper()
        or row.get("ISIN", "").upper().startswith("DUMMY")
        for row in rows
    )
    issues = []
    if (report_date - latest_update).days > 365:
        issues.append(
            f"latest CM UpdDt {latest_update.isoformat()} is more than one year "
            f"older than catalogue date {report_date.isoformat()}"
        )
    return {
        "row_count": len(rows),
        "distinct_symbols": len({row["TckrSymb"] for row in rows if row.get("TckrSymb")}),
        "latest_update_date_utc": latest_update.isoformat(),
        "test_or_dummy_rows": test_rows,
        "issues": issues,
    }


def inspect_fo_quality(expanded: bytes, trading_date: str) -> dict:
    required = {
        "FinInstrmNm", "TckrSymb", "XpryDt", "DelFlg",
        "PrtdToTrad", "TradgStsNrmlMkt", "ElgbltyNrmlMkt",
    }
    rows = _csv_rows(expanded, required, "FO contract master")
    report_date = datetime.strptime(trading_date, "%d-%b-%Y").date()
    stock_rows = [
        row for row in rows
        if row.get("FinInstrmNm") in {"FUTSTK", "OPTSTK"}
        and row.get("DelFlg") != "Y"
        and row.get("TckrSymb")
        and "NSETEST" not in row.get("TckrSymb", "").upper()
    ]
    expiries = []
    future_rows = 0
    for row in stock_rows:
        raw_expiry = row.get("XpryDt", "")
        if not raw_expiry.isdigit() or int(raw_expiry) <= 0:
            continue
        expiry = datetime.fromtimestamp(
            int(raw_expiry), tz=timezone.utc
        ).date()
        expiries.append(expiry)
        if expiry >= report_date and row.get("PrtdToTrad") == "1":
            future_rows += 1
    latest_expiry = max(expiries).isoformat() if expiries else None
    issues = []
    if not stock_rows:
        issues.append("no non-test individual-stock FUTSTK/OPTSTK contracts found")
    elif future_rows == 0:
        issues.append(
            "no non-test individual-stock contracts expire on or after catalogue date "
            f"{report_date.isoformat()}; latest stock expiry is {latest_expiry}"
        )
    return {
        "row_count": len(rows),
        "stock_contract_rows": len(stock_rows),
        "stock_symbols": len({row["TckrSymb"] for row in stock_rows}),
        "latest_stock_expiry_date_utc": latest_expiry,
        "stock_contract_rows_expiring_on_or_after_catalogue_date": future_rows,
        "test_symbol_rows": sum(
            "NSETEST" in row.get("TckrSymb", "").upper() for row in rows
        ),
        "issues": issues,
    }


def _capture(record: dict, page_url: str, output_dir: Path) -> dict:
    payload = _get(
        record["download_url"],
        referer=page_url,
        accept="application/gzip,application/octet-stream,*/*",
    )
    if not payload:
        raise ValueError(f"{record['dataset']}: downloaded empty file")
    try:
        expanded = gzip.decompress(payload)
    except OSError as exc:
        raise ValueError(f"{record['dataset']}: response is not a valid gzip file") from exc
    if not expanded:
        raise ValueError(f"{record['dataset']}: gzip expanded to an empty file")
    try:
        preview = expanded[:8192].decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{record['dataset']}: decompressed CSV is not UTF-8") from exc
    lines = preview.splitlines()
    raw_hash = hashlib.sha256(payload).hexdigest()
    expanded_hash = hashlib.sha256(expanded).hexdigest()
    date_token = datetime.strptime(record["trading_date"], "%d-%b-%Y").strftime("%Y%m%d")
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{record['dataset']}_{date_token}_{raw_hash[:16]}.csv.gz"
    raw_path.write_bytes(payload)
    return {
        **record,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "compressed_bytes": len(payload),
        "decompressed_bytes": len(expanded),
        "raw_sha256": raw_hash,
        "decompressed_sha256": expanded_hash,
        "raw_artifact_path": str(raw_path),
        "preview_lines": lines[:4],
        "preview_truncated": len(expanded) > 8192,
    }


def ingest(output_dir: Path = Path("artifacts/nse-reference")) -> dict:
    # Load both official report pages first to establish the same-origin session.
    _get(CM_REPORT_PAGE, referer=BASE_URL + "/", accept="text/html,*/*")
    _get(FO_REPORT_PAGE, referer=CM_REPORT_PAGE, accept="text/html,*/*")

    cm_url = REPORT_API + "?" + urlencode({"key": "CM"})
    fo_url = REPORT_API + "?" + urlencode({"key": "FO"})
    cm_catalog = parse_catalog(_get(cm_url, CM_REPORT_PAGE, "application/json,*/*"))
    fo_catalog = parse_catalog(_get(fo_url, FO_REPORT_PAGE, "application/json,*/*"))

    cm_record = select_cm_security_master(cm_catalog)
    fo_record = select_fo_contract_master(fo_catalog)
    cm_capture = _capture(cm_record, CM_REPORT_PAGE, output_dir)
    fo_capture = _capture(fo_record, FO_REPORT_PAGE, output_dir)
    cm_expanded = gzip.decompress(
        (output_dir / "raw" / cm_capture["raw_artifact_path"].split("/")[-1]).read_bytes()
    )
    fo_expanded = gzip.decompress(
        (output_dir / "raw" / fo_capture["raw_artifact_path"].split("/")[-1]).read_bytes()
    )
    quality = {
        "cm_security_master": inspect_cm_quality(cm_expanded, cm_record["trading_date"]),
        "fo_contract_master": inspect_fo_quality(fo_expanded, fo_record["trading_date"]),
    }
    quality_issues = [
        f"{dataset}: {issue}"
        for dataset, result in quality.items()
        for issue in result["issues"]
    ]

    report = {
        "generated_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "publisher": "National Stock Exchange of India",
        "source_pages": {"CM": CM_REPORT_PAGE, "FO": FO_REPORT_PAGE},
        "catalogue_apis": {"CM": cm_url, "FO": fo_url},
        "catalogue_current_dates": {
            "CM": cm_catalog["currentDate"],
            "FO": fo_catalog["currentDate"],
        },
        "captures": [cm_capture, fo_capture],
        "scope_note": (
            "CM security master is NSE-listed reference data; FO contract master is "
            "NSE-exclusive contract metadata. F&O eligibility must be derived from "
            "active stock-underlying contracts, not inferred from the CM security file."
        ),
        "schema_status": "observed_mii_headers_validated; semantic_freshness_checks_applied",
        "data_quality_status": "quarantined" if quality_issues else "pass",
        "data_quality": quality,
        "data_quality_issues": quality_issues,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-reference"))
    args = parser.parse_args()
    print(json.dumps(ingest(args.output_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
