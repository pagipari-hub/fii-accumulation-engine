"""Capture NSE participant-wise F&O OI/volume and aggregate FII derivatives reports.

This is a source-capture/schema-discovery stage. It does not assign aggregate
participant positions to individual stocks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import xlrd

from fii_engine.nse_fo_bhavcopy import (
    ARCHIVE_HOST,
    REPORT_PAGE,
    _get,
    _parse_catalogue_date,
    fetch_catalogue,
)

REPORTS = {
    "participant_oi": {
        "file_key": "FO-PARTICIPANTWISE-OI",
        "filename_re": re.compile(r"^fao_participant_oi_(?P<date>\d{8})\.csv$"),
        "extension": ".csv",
        "scope": "participant_category_aggregate_not_stock_specific",
    },
    "participant_volume": {
        "file_key": "FO-PARTICIPANTWISE-TRADING-VOL",
        "filename_re": re.compile(r"^fao_participant_vol_(?P<date>\d{8})\.csv$"),
        "extension": ".csv",
        "scope": "participant_category_aggregate_not_stock_specific",
    },
    "fii_derivatives_statistics": {
        "file_key": "FO-FII-DERIVATIVE-STAT",
        "filename_re": re.compile(
            r"^fii_stats_(?P<date>\d{2}-[A-Za-z]{3}-\d{4})\.xls$"
        ),
        "extension": ".xls",
        "scope": "aggregate_fii_derivatives_only",
    },
}


def select_report(catalogue: dict, dataset: str) -> dict:
    if dataset not in REPORTS:
        raise ValueError(f"unsupported NSE aggregate report: {dataset}")
    spec = REPORTS[dataset]
    candidates = []
    for group_name in ("CurrentDay", "PreviousDay"):
        for item in catalogue.get(group_name, []):
            if isinstance(item, dict) and item.get("fileKey") == spec["file_key"]:
                record = dict(item)
                record["_catalogue_group"] = group_name
                candidates.append(record)
    if not candidates:
        raise ValueError(f"NSE catalogue has no {spec['file_key']} report")

    candidates.sort(key=lambda item: _parse_catalogue_date(item.get("tradingDate")))
    selected = candidates[-1]
    trading_date = _parse_catalogue_date(selected.get("tradingDate"))
    filename = selected.get("fileActlName")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError(f"{dataset}: unsafe NSE report filename {filename!r}")
    match = spec["filename_re"].fullmatch(filename)
    if not match:
        raise ValueError(f"{dataset}: unexpected NSE report filename {filename!r}")
    date_token = match.group("date")
    if spec["extension"] == ".csv":
        filename_date = datetime.strptime(date_token, "%d%m%Y").date()
    else:
        filename_date = datetime.strptime(date_token, "%d-%b-%Y").date()
    if filename_date != trading_date:
        raise ValueError(
            f"{dataset}: catalogue/filename date mismatch "
            f"{trading_date} vs {filename_date}"
        )
    file_path = selected.get("filePath")
    parsed = urlparse(file_path or "")
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARCHIVE_HOST
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{dataset}: untrusted NSE archive path {file_path!r}")
    return {
        "dataset": dataset,
        "report_key": spec["file_key"],
        "display_name": selected.get("displayName"),
        "catalogue_group": selected["_catalogue_group"],
        "trading_date": trading_date.isoformat(),
        "filename": filename,
        "download_url": file_path.rstrip("/") + "/" + filename,
        "reported_file_size": selected.get("fileSize"),
        "file_position": selected.get("filePosition"),
        "file_hash_key": selected.get("fileHashKey"),
        "attribution_scope": spec["scope"],
    }


def inspect_csv(payload: bytes, dataset: str) -> dict:
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{dataset}: CSV is not UTF-8") from exc
    reader = csv.reader(io.StringIO(decoded, newline=""))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise ValueError(f"{dataset}: CSV has no header/data rows")
    header = [cell.strip() for cell in rows[0]]
    if not any(header) or len(header) != len(set(header)):
        raise ValueError(f"{dataset}: invalid or duplicate CSV header")
    width = len(header)
    inconsistent = [
        index + 1 for index, row in enumerate(rows[1:], start=1)
        if len(row) != width
    ]
    if inconsistent:
        raise ValueError(
            f"{dataset}: CSV row width differs from header at rows {inconsistent[:10]}"
        )
    return {
        "format": "csv",
        "row_count": len(rows) - 1,
        "column_count": width,
        "columns": header,
        "header_sha256": hashlib.sha256(
            (",".join(header) + "\n").encode("utf-8")
        ).hexdigest(),
        "preview_rows": rows[1:4],
    }


def inspect_xls(payload: bytes, dataset: str) -> dict:
    if not payload.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError(f"{dataset}: response is not an OLE2 .xls workbook")
    try:
        workbook = xlrd.open_workbook(file_contents=payload, on_demand=True)
    except Exception as exc:
        raise ValueError(f"{dataset}: could not open NSE .xls workbook: {exc}") from exc
    sheets = []
    for sheet in workbook.sheets():
        preview = []
        for row_index in range(min(sheet.nrows, 12)):
            values = []
            for col_index in range(min(sheet.ncols, 16)):
                value = sheet.cell_value(row_index, col_index)
                values.append(value)
            preview.append(values)
        sheets.append({
            "name": sheet.name,
            "row_count": sheet.nrows,
            "column_count": sheet.ncols,
            "preview_rows": preview,
        })
    workbook.release_resources()
    if not sheets:
        raise ValueError(f"{dataset}: workbook contains no worksheets")
    return {"format": "xls", "sheet_count": len(sheets), "sheets": sheets}


def capture_dataset(dataset: str, catalogue: dict, root: Path) -> dict:
    selected = select_report(catalogue, dataset)
    raw = _get(
        selected["download_url"],
        REPORT_PAGE,
        "text/csv,application/vnd.ms-excel,application/octet-stream,*/*",
    )
    if not raw:
        raise ValueError(f"{dataset}: NSE report download is empty")
    raw_hash = hashlib.sha256(raw).hexdigest()
    retrieved_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )

    raw_dir = root / "data/raw/fno_aggregate_reports"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / (
        f"{selected['trading_date']}_{raw_hash[:16]}_{selected['filename']}"
    )
    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_hash:
        raise RuntimeError(f"{dataset}: immutable raw path hash mismatch")
    # Preserve the original exchange bytes before attempting any inspection.
    if not raw_path.exists():
        raw_path.write_bytes(raw)

    inspection = None
    inspection_error = None
    try:
        if selected["filename"].lower().endswith(".csv"):
            inspection = inspect_csv(raw, dataset)
        else:
            inspection = inspect_xls(raw, dataset)
    except (ValueError, OSError) as exc:
        inspection_error = str(exc)

    manifest = {
        "dataset": dataset,
        "publisher": "National Stock Exchange of India",
        "reference_url": REPORT_PAGE,
        **selected,
        "retrieved_at_utc": retrieved_at,
        "raw_path": str(raw_path),
        "raw_bytes": len(raw),
        "raw_sha256": raw_hash,
        "inspection": inspection,
        "inspection_error": inspection_error,
        "parser_stage": "source_capture_and_schema_discovery",
    }
    manifest_dir = raw_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{dataset}_{selected['trading_date']}_{raw_hash[:16]}.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("raw_sha256") != raw_hash:
            raise RuntimeError(f"{dataset}: immutable manifest hash mismatch")
    else:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
        )
    return {
        "dataset": dataset,
        "trading_date": selected["trading_date"],
        "filename": selected["filename"],
        "raw_bytes": len(raw),
        "raw_sha256": raw_hash,
        "raw_path": str(raw_path),
        "manifest_path": str(manifest_path),
        "inspection": inspection,
        "inspection_error": inspection_error,
    }


def ingest(root: Path = Path(".")) -> dict:
    catalogue = fetch_catalogue()
    results = [
        capture_dataset(dataset, catalogue, root)
        for dataset in REPORTS
    ]
    current = {
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "datasets": results,
        "attribution_boundary": (
            "Participant reports are category aggregates, not participant-by-stock "
            "positions. FII statistics are aggregate FII derivatives data only."
        ),
    }
    current_path = root / "data/raw/fno_aggregate_reports/current_manifest.json"
    current_path.write_text(json.dumps(current, indent=2, default=str) + "\n", encoding="utf-8")
    errors = [
        f"{item['dataset']}: {item['inspection_error']}"
        for item in results if item.get("inspection_error")
    ]
    if errors:
        raise ValueError(
            "NSE aggregate report schema inspection needs review: " + "; ".join(errors)
        )
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2, default=str))


if __name__ == "__main__":
    main()
