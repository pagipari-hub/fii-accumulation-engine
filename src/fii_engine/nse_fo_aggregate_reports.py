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


PARTICIPANT_COLUMNS = [
    "Client Type",
    "Future Index Long",
    "Future Index Short",
    "Future Stock Long",
    "Future Stock Short",
    "Option Index Call Long",
    "Option Index Put Long",
    "Option Index Call Short",
    "Option Index Put Short",
    "Option Stock Call Long",
    "Option Stock Put Long",
    "Option Stock Call Short",
    "Option Stock Put Short",
    "Total Long Contracts",
    "Total Short Contracts",
]


def inspect_csv(
    payload: bytes, dataset: str, expected_trade_date: str | None = None
) -> dict:
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{dataset}: CSV is not UTF-8") from exc

    # NSE participant files have a malformed-quoted title line before the
    # actual tabular header. Parse that title separately; do not let its
    # embedded comma split the title into fake CSV columns.
    lines = [line.rstrip("\r") for line in decoded.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError(f"{dataset}: CSV has no title/header/data structure")
    title = lines[0].strip().rstrip(",").strip().strip('"').strip()
    expected_title = {
        "participant_oi": "Open Interest",
        "participant_volume": "Trading Volume",
    }.get(dataset)
    if expected_title and expected_title.lower() not in title.lower():
        raise ValueError(f"{dataset}: unexpected report title: {title!r}")
    date_match = re.search(
        r"as on\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})$", title, re.IGNORECASE
    )
    if not date_match:
        raise ValueError(f"{dataset}: report title has no recognizable as-of date")
    title_date = datetime.strptime(date_match.group(1), "%b %d, %Y").date().isoformat()
    if expected_trade_date and title_date != expected_trade_date:
        raise ValueError(
            f"{dataset}: title date {title_date} does not match catalogue date "
            f"{expected_trade_date}"
        )

    reader = csv.reader(io.StringIO("\n".join(lines[1:]), newline=""))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise ValueError(f"{dataset}: CSV has no participant data rows")
    header = [cell.strip() for cell in rows[0]]
    if header != PARTICIPANT_COLUMNS:
        raise ValueError(
            f"{dataset}: participant CSV schema drift; observed header {header!r}"
        )
    data_rows = []
    seen_categories = set()
    for row_number, raw_row in enumerate(rows[1:], start=3):
        if len(raw_row) != len(header):
            raise ValueError(
                f"{dataset}: row {row_number} has {len(raw_row)} fields; "
                f"expected {len(header)}"
            )
        category = raw_row[0].strip()
        if category not in {"Client", "DII", "FII", "Pro", "TOTAL"}:
            raise ValueError(
                f"{dataset}: unknown participant category {category!r} "
                f"at row {row_number}"
            )
        if category in seen_categories:
            raise ValueError(f"{dataset}: duplicate participant category {category!r}")
        seen_categories.add(category)
        values = {}
        for column, raw_value in zip(header[1:], raw_row[1:]):
            value = raw_value.strip()
            try:
                number = int(value)
            except ValueError as exc:
                raise ValueError(
                    f"{dataset}: non-integer {column!r} for {category}: {value!r}"
                ) from exc
            if number < 0:
                raise ValueError(
                    f"{dataset}: negative contract count {column!r} for {category}"
                )
            values[column] = number
        data_rows.append({"participant_category": category, **values})

    expected_categories = {"Client", "DII", "FII", "Pro", "TOTAL"}
    if seen_categories != expected_categories:
        raise ValueError(
            f"{dataset}: participant categories {sorted(seen_categories)} "
            f"do not match expected {sorted(expected_categories)}"
        )
    by_category = {row["participant_category"]: row for row in data_rows}
    participant_rows = [by_category[name] for name in ("Client", "DII", "FII", "Pro")]
    total_row = by_category["TOTAL"]
    numeric_columns = header[1:]
    column_reconciliation = {
        column: {
            "participant_sum": sum(row[column] for row in participant_rows),
            "reported_total": total_row[column],
            "difference": total_row[column] - sum(row[column] for row in participant_rows),
        }
        for column in numeric_columns
    }
    long_columns = [column for column in numeric_columns if column.endswith(" Long")]
    short_columns = [column for column in numeric_columns if column.endswith(" Short")]
    row_reconciliation = {}
    for row in data_rows:
        category = row["participant_category"]
        long_sum = sum(row[column] for column in long_columns)
        short_sum = sum(row[column] for column in short_columns)
        row_reconciliation[category] = {
            "long_component_sum": long_sum,
            "reported_total_long": row["Total Long Contracts"],
            "long_difference": row["Total Long Contracts"] - long_sum,
            "short_component_sum": short_sum,
            "reported_total_short": row["Total Short Contracts"],
            "short_difference": row["Total Short Contracts"] - short_sum,
        }
    differences = [
        abs(item["difference"]) for item in column_reconciliation.values()
    ] + [
        abs(item[key])
        for item in row_reconciliation.values()
        for key in ("long_difference", "short_difference")
    ]
    return {
        "format": "csv",
        "title": title,
        "trade_date_in_title": title_date,
        "units_as_labelled": "number_of_contracts",
        "row_count": len(data_rows),
        "participant_categories": [row["participant_category"] for row in data_rows],
        "column_count": len(header),
        "columns": header,
        "header_sha256": hashlib.sha256(
            (",".join(header) + "\n").encode("utf-8")
        ).hexdigest(),
        "participant_rows": participant_rows,
        "reported_total_row": total_row,
        "reconciliation": {
            "status": "exact_match" if not any(differences) else "difference_observed",
            "max_absolute_difference": max(differences, default=0),
            "column_totals": column_reconciliation,
            "row_component_totals": row_reconciliation,
        },
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
        for row_index in range(min(sheet.nrows, 50)):
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
            inspection = inspect_csv(raw, dataset, selected["trading_date"])
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
