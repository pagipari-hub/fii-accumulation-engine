"""Backfill NSE's date-addressable historical F&O aggregate reports.

Uses the public NSE archive file paths rather than guessing the undocumented
/api/reports category/section selector. Every payload must pass the existing
strict schema and embedded trade-date validators before it is retained.
Aggregate F&O reports are context-only, never stock-level FPI attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fii_engine.nse_fo_aggregate_reports import inspect_csv, parse_fii_stats_xls
from fii_engine.nse_fo_bhavcopy import REPORT_PAGE, _get

ARCHIVE_BASE = "https://nsearchives.nseindia.com"
REPORTS = {
    "participant_oi": {
        "path": "/content/nsccl/fao_participant_oi_{date}.csv",
        "extension": ".csv",
        "scope": "participant_category_aggregate_not_stock_specific",
    },
    "participant_volume": {
        "path": "/content/nsccl/fao_participant_vol_{date}.csv",
        "extension": ".csv",
        "scope": "participant_category_aggregate_not_stock_specific",
    },
    "fii_derivatives_statistics": {
        "path": "/content/fo/fii_stats_{date}.xls",
        "extension": ".xls",
        "scope": "aggregate_fii_derivatives_only",
    },
}


def build_archive_url(dataset: str, trade_date: date) -> str:
    if dataset not in REPORTS:
        raise ValueError(f"unsupported historical F&O dataset: {dataset}")
    if dataset == "fii_derivatives_statistics":
        date_token = trade_date.strftime("%d-%b-%Y")
    else:
        date_token = trade_date.strftime("%d%m%Y")
    return ARCHIVE_BASE + REPORTS[dataset]["path"].format(date=date_token)


def _unwrap_download(payload: bytes, dataset: str) -> tuple[bytes, str | None]:
    if not payload:
        raise ValueError(f"{dataset}: NSE returned an empty archive response")
    if not payload.startswith(b"PK\\x03\\x04"):
        return payload, None
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                raise ValueError(
                    f"{dataset}: expected one file in report ZIP, found {len(members)}"
                )
            member = members[0]
            if member.file_size <= 0 or member.file_size > 50_000_000:
                raise ValueError(f"{dataset}: report ZIP member size outside safety bounds")
            return archive.read(member), member.filename
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{dataset}: response starts as ZIP but is invalid") from exc


def _validate(dataset: str, payload: bytes, expected_date: str) -> dict:
    if dataset in {"participant_oi", "participant_volume"}:
        return inspect_csv(payload, dataset, expected_trade_date=expected_date)
    return parse_fii_stats_xls(payload, expected_trade_date=expected_date)


def fetch_one(dataset: str, trade_date: date) -> tuple[dict, bytes, bytes]:
    url = build_archive_url(dataset, trade_date)
    response = _get(
        url, REPORT_PAGE, "application/octet-stream,application/vnd.ms-excel,*/*"
    )
    extracted, member_name = _unwrap_download(response, dataset)
    inspection = _validate(dataset, extracted, trade_date.isoformat())
    return ({
        "dataset": dataset,
        "trade_date": trade_date.isoformat(),
        "request_url": url,
        "archive_member": member_name,
        "raw_bytes": len(response),
        "payload_bytes": len(extracted),
        "archive_sha256": hashlib.sha256(response).hexdigest(),
        "payload_sha256": hashlib.sha256(extracted).hexdigest(),
        "inspection": inspection,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution_scope": REPORTS[dataset]["scope"],
    }, response, extracted)


def _save_immutable(path: Path, payload: bytes, digest: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"immutable archive collision: {path}")
        return
    path.write_bytes(payload)


def _candidate_dates(end_date: date, max_calendar_days: int):
    examined = 0
    current = end_date
    while examined < max_calendar_days:
        examined += 1
        if current.weekday() < 5:
            yield current
        current -= timedelta(days=1)


def run(root: Path, sessions: int, end_date: date, max_calendar_days: int) -> dict:
    if sessions < 1 or sessions > 120:
        raise ValueError("sessions must be between 1 and 120")
    if max_calendar_days < sessions or max_calendar_days > 365:
        raise ValueError("max_calendar_days must be between sessions and 365")
    root = root.resolve()
    manifests = []
    unavailable = []
    complete_dates = 0
    # Warm the NSE session before requesting files from its archive subdomain.
    _get(REPORT_PAGE, "https://www.nseindia.com/", "text/html,*/*")
    for trade_date in _candidate_dates(end_date, max_calendar_days):
        date_records = []
        date_failed = False
        for dataset, spec in REPORTS.items():
            try:
                record, archive_bytes, payload_bytes = fetch_one(dataset, trade_date)
                digest = record["archive_sha256"]
                raw_path = (
                    root / "data/raw/nse_fo_historical"
                    / trade_date.isoformat() / f"{dataset}_{digest[:16]}.bin"
                )
                _save_immutable(raw_path, archive_bytes, digest)
                payload_digest = record["payload_sha256"]
                payload_path = (
                    root / "data/raw/nse_fo_historical"
                    / trade_date.isoformat()
                    / f"{dataset}_{payload_digest[:16]}{spec['extension']}"
                )
                _save_immutable(payload_path, payload_bytes, payload_digest)
                record["raw_path"] = str(raw_path.relative_to(root))
                record["payload_path"] = str(payload_path.relative_to(root))
                date_records.append(record)
            except Exception as exc:
                date_failed = True
                unavailable.append({
                    "trade_date": trade_date.isoformat(),
                    "dataset": dataset,
                    "status": "unavailable_or_unvalidated",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        manifests.extend(date_records)
        if not date_failed and len(date_records) == len(REPORTS):
            complete_dates += 1
        if complete_dates >= sessions:
            break
        time.sleep(0.25)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_complete_sessions": sessions,
        "complete_sessions": complete_dates,
        "datasets": sorted(REPORTS),
        "records": manifests,
        "unavailable": unavailable,
        "coverage_status": "target_met" if complete_dates >= sessions else "partial",
        "important_scope_note": (
            "Participant reports are participant-category aggregates; FII derivatives "
            "statistics are aggregate FII derivatives data. None proves stock-specific "
            "FPI cash purchases or may be attributed to individual securities."
        ),
    }
    manifest_path = root / "data/raw/nse_fo_historical/historical_manifest.json"
    encoded = (json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n").encode()
    digest = hashlib.sha256(encoded).hexdigest()
    _save_immutable(
        root / "data/raw/nse_fo_historical/manifests"
        / f"historical_manifest_{digest[:16]}.json",
        encoded,
        digest,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(encoded)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--max-calendar-days", type=int, default=120)
    args = parser.parse_args()
    summary = run(args.repo_root, args.sessions, args.end_date, args.max_calendar_days)
    print(json.dumps({
        "coverage_status": summary["coverage_status"],
        "requested_complete_sessions": summary["requested_complete_sessions"],
        "complete_sessions": summary["complete_sessions"],
        "dataset_record_count": len(summary["records"]),
        "unavailable_count": len(summary["unavailable"]),
        "manifest": "data/raw/nse_fo_historical/historical_manifest.json",
    }, indent=2))
    if summary["coverage_status"] != "target_met":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
