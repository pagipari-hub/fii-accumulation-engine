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
from decimal import Decimal, InvalidOperation
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

PARTICIPANT_COMPONENTS = {
    "Future Index Long": ("index_futures", "", "long"),
    "Future Index Short": ("index_futures", "", "short"),
    "Future Stock Long": ("stock_futures", "", "long"),
    "Future Stock Short": ("stock_futures", "", "short"),
    "Option Index Call Long": ("index_options", "call", "long"),
    "Option Index Put Long": ("index_options", "put", "long"),
    "Option Index Call Short": ("index_options", "call", "short"),
    "Option Index Put Short": ("index_options", "put", "short"),
    "Option Stock Call Long": ("stock_options", "call", "long"),
    "Option Stock Put Long": ("stock_options", "put", "long"),
    "Option Stock Call Short": ("stock_options", "call", "short"),
    "Option Stock Put Short": ("stock_options", "put", "short"),
}


def normalize_participant_report(inspection: dict, dataset: str, trade_date: str) -> list[dict]:
    metric = (
        "open_interest_contracts"
        if dataset == "participant_oi"
        else "traded_contracts"
    )
    normalized = []
    for participant in inspection["participant_rows"]:
        for column, (instrument_group, option_type, source_side) in PARTICIPANT_COMPONENTS.items():
            normalized.append({
                "trade_date": trade_date,
                "dataset": dataset,
                "metric": metric,
                "unit": "contracts",
                "participant_category": participant["participant_category"],
                "instrument_group": instrument_group,
                "option_type": option_type,
                "source_side": source_side,
                "contracts": participant[column],
            })
    return normalized


def _xls_integer(value, label: str) -> int:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"FII statistics {label} is not numeric: {value!r}") from exc
    if number != number.to_integral_value() or number < 0:
        raise ValueError(f"FII statistics {label} is not a non-negative integer: {value!r}")
    return int(number)


def _xls_amount(value, label: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"FII statistics {label} is not numeric: {value!r}") from exc
    if number < 0:
        raise ValueError(f"FII statistics {label} is negative: {value!r}")
    return format(number.quantize(Decimal("0.01")), ".2f")


def parse_fii_stats_xls(payload: bytes, expected_trade_date: str) -> dict:
    if not payload.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("FII statistics response is not an OLE2 .xls workbook")
    try:
        workbook = xlrd.open_workbook(file_contents=payload, on_demand=True)
    except Exception as exc:
        raise ValueError(f"could not open NSE FII statistics workbook: {exc}") from exc
    try:
        if workbook.nsheets != 1:
            raise ValueError(f"expected one FII statistics sheet; found {workbook.nsheets}")
        sheet = workbook.sheet_by_index(0)
        if sheet.ncols != 7 or sheet.nrows < 23:
            raise ValueError(
                f"FII statistics workbook dimensions changed: {sheet.nrows}x{sheet.ncols}"
            )
        title = str(sheet.cell_value(0, 0)).strip()
        match = re.fullmatch(
            r"FII DERIVATIVES STATISTICS FOR (\d{2}-[A-Za-z]{3}-\d{4})",
            title,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"unexpected FII statistics title: {title!r}")
        report_date = datetime.strptime(match.group(1), "%d-%b-%Y").date().isoformat()
        if report_date != expected_trade_date:
            raise ValueError(
                f"FII statistics title date {report_date} does not match "
                f"catalogue date {expected_trade_date}"
            )
        header_one = [str(value).strip() if value not in ("", None) else "" for value in sheet.row_values(1)]
        header_two = [str(value).strip() if value not in ("", None) else "" for value in sheet.row_values(2)]
        expected_one = ["", "BUY", "BUY", "SELL", "SELL", "OPEN INTEREST AT THE END OF THE DAY", ""]
        expected_two = ["", "No. of contracts", "Amt in Crores", "No. of contracts", "Amt in Crores", "No. of contracts", "Amt in Crores"]
        if header_one != expected_one or header_two != expected_two:
            raise ValueError("FII statistics workbook header schema drift")

        section_map = {
            "INDEX FUTURES": "index_futures",
            "INDEX OPTIONS": "index_options",
            "STOCK FUTURES": "stock_futures",
            "STOCK OPTIONS": "stock_options",
        }
        section_totals = {}
        detail_records = []
        current_section = None
        notes = []
        for row_index in range(3, sheet.nrows):
            values = sheet.row_values(row_index)
            label = str(values[0]).strip().upper() if values[0] not in ("", None) else ""
            if not label:
                continue
            if label == "NOTES:" or row_index >= 26:
                note = " ".join(str(value).strip() for value in values if value not in ("", None))
                if note and note.upper() != "NOTES:":
                    notes.append(note)
                continue
            if label in section_map:
                current_section = section_map[label]
                if current_section in section_totals:
                    raise ValueError(f"duplicate FII statistics section {label}")
                record_level = "section_total"
                underlying = "ALL_INDICES" if current_section.startswith("index_") else "ALL_STOCKS"
            elif current_section and (
                label.endswith(" FUTURES") or label.endswith(" OPTIONS")
            ):
                record_level = "underlying_detail"
                underlying = label.rsplit(" ", 1)[0]
                if current_section.startswith("stock_"):
                    raise ValueError(f"unexpected stock-level FII detail row: {label}")
            else:
                raise ValueError(f"unrecognized FII statistics row {row_index + 1}: {label!r}")

            record = {
                "trade_date": report_date,
                "instrument_group": current_section,
                "underlying": underlying,
                "record_level": record_level,
                "buy_contracts": _xls_integer(values[1], f"{label} buy contracts"),
                "buy_amount_crore": _xls_amount(values[2], f"{label} buy amount"),
                "sell_contracts": _xls_integer(values[3], f"{label} sell contracts"),
                "sell_amount_crore": _xls_amount(values[4], f"{label} sell amount"),
                "open_interest_contracts": _xls_integer(values[5], f"{label} open interest contracts"),
                "open_interest_amount_crore": _xls_amount(values[6], f"{label} open interest amount"),
            }
            if record_level == "section_total":
                section_totals[current_section] = record
            else:
                detail_records.append(record)

        if set(section_totals) != set(section_map.values()):
            raise ValueError(
                f"FII statistics sections incomplete: {sorted(section_totals)}"
            )
        records = list(section_totals.values()) + detail_records
        details_by_group = {}
        for record in detail_records:
            details_by_group.setdefault(record["instrument_group"], []).append(record)

        reconciliation = {}
        for group in ("index_futures", "index_options"):
            details = details_by_group.get(group, [])
            if not details:
                raise ValueError(f"FII statistics missing underlying details for {group}")
            total = section_totals[group]
            metrics = (
                "buy_contracts", "buy_amount_crore",
                "sell_contracts", "sell_amount_crore",
                "open_interest_contracts", "open_interest_amount_crore",
            )
            group_diff = {}
            for metric in metrics:
                detail_sum = sum(
                    (Decimal(str(item[metric])) for item in details), Decimal("0")
                )
                total_value = Decimal(str(total[metric]))
                group_diff[metric] = format(total_value - detail_sum, "f")
            reconciliation[group] = group_diff

        for group in ("stock_futures", "stock_options"):
            if details_by_group.get(group):
                raise ValueError(f"unexpected stock-level details for {group}")

        for row_index in range(26, sheet.nrows):
            values = sheet.row_values(row_index)
            note = " ".join(str(value).strip() for value in values if value not in ("", None))
            if note and note.upper() != "NOTES:":
                notes.append(note)

        return {
            "format": "xls",
            "title": title,
            "trade_date_in_title": report_date,
            "sheet_name": sheet.name,
            "sheet_rows": sheet.nrows,
            "sheet_columns": sheet.ncols,
            "normalized_records": records,
            "section_reconciliation": reconciliation,
            "notes": notes,
        }
    finally:
        workbook.release_resources()


def reconcile_fii_oi(participant_oi: dict, fii_statistics: dict) -> dict:
    fii_row = next(
        row for row in participant_oi["participant_rows"]
        if row["participant_category"] == "FII"
    )
    section_totals = {
        row["instrument_group"]: row
        for row in fii_statistics["normalized_records"]
        if row["record_level"] == "section_total"
    }
    source_values = {
        "index_futures": (
            fii_row["Future Index Long"] + fii_row["Future Index Short"]
        ),
        "stock_futures": (
            fii_row["Future Stock Long"] + fii_row["Future Stock Short"]
        ),
        "index_options": sum(
            fii_row[column] for column in (
                "Option Index Call Long", "Option Index Put Long",
                "Option Index Call Short", "Option Index Put Short",
            )
        ),
        "stock_options": sum(
            fii_row[column] for column in (
                "Option Stock Call Long", "Option Stock Put Long",
                "Option Stock Call Short", "Option Stock Put Short",
            )
        ),
    }
    comparisons = {}
    for group, participant_total in source_values.items():
        report_total = section_totals[group]["open_interest_contracts"]
        comparisons[group] = {
            "participant_oi_long_plus_short": participant_total,
            "fii_statistics_open_interest_contracts": report_total,
            "difference": report_total - participant_total,
            "status": "exact_match" if report_total == participant_total else "difference_observed",
        }
    return {
        "comparison": "NSE participant-wise FII OI long+short vs NSE FII statistics end-of-day OI",
        "trade_date": participant_oi["trade_date_in_title"],
        "comparisons": comparisons,
        "max_absolute_difference": max(
            abs(item["difference"]) for item in comparisons.values()
        ),
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
            inspection["normalized_records"] = normalize_participant_report(
                inspection, dataset, selected["trading_date"]
            )
        else:
            preview = inspect_xls(raw, dataset)
            parsed = parse_fii_stats_xls(raw, selected["trading_date"])
            inspection = {**preview, **parsed}
    except (ValueError, OSError) as exc:
        inspection_error = str(exc)

    processed_path = None
    processed_hash = None
    normalized_row_count = 0
    if inspection is not None:
        normalized_records = inspection.get("normalized_records", [])
        if normalized_records:
            processed_dir = root / "data/processed/fno_aggregate_reports"
            processed_dir.mkdir(parents=True, exist_ok=True)
            processed_path = processed_dir / (
                f"{dataset}_{selected['trading_date']}_{raw_hash[:16]}.csv"
            )
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=list(normalized_records[0]))
            writer.writeheader()
            writer.writerows(normalized_records)
            processed_bytes = buffer.getvalue().encode("utf-8")
            processed_hash = hashlib.sha256(processed_bytes).hexdigest()
            if processed_path.exists():
                if hashlib.sha256(processed_path.read_bytes()).hexdigest() != processed_hash:
                    raise RuntimeError(f"{dataset}: immutable normalized path hash mismatch")
            else:
                processed_path.write_bytes(processed_bytes)
            normalized_row_count = len(normalized_records)

    manifest = {
        "dataset": dataset,
        "publisher": "National Stock Exchange of India",
        "reference_url": REPORT_PAGE,
        **selected,
        "retrieved_at_utc": retrieved_at,
        "raw_path": str(raw_path),
        "raw_bytes": len(raw),
        "raw_sha256": raw_hash,
        "processed_path": str(processed_path) if processed_path else None,
        "processed_sha256": processed_hash,
        "normalized_row_count": normalized_row_count,
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
        "processed_path": str(processed_path) if processed_path else None,
        "processed_sha256": processed_hash,
        "normalized_row_count": normalized_row_count,
        "inspection": inspection,
        "inspection_error": inspection_error,
    }


def ingest(root: Path = Path(".")) -> dict:
    catalogue = fetch_catalogue()
    results = [
        capture_dataset(dataset, catalogue, root)
        for dataset in REPORTS
    ]
    by_dataset = {item["dataset"]: item for item in results}
    cross_source_reconciliation = reconcile_fii_oi(
        by_dataset["participant_oi"]["inspection"],
        by_dataset["fii_derivatives_statistics"]["inspection"],
    )
    current = {
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "datasets": results,
        "cross_source_reconciliation": cross_source_reconciliation,
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
