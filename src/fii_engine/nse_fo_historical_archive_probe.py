"""Probe NSE's date-selectable historical F&O report archive.

The daily-reports catalogue is intentionally not used here: it exposes only
CurrentDay/PreviousDay. This probe uses the separate /api/reports archive
selector and accepts a report only after its embedded trade date and existing
schema validator agree. Aggregate reports are context-only, never stock-level
FPI attribution.
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
from urllib.parse import urlencode

from fii_engine.nse_fo_aggregate_reports import inspect_csv, parse_fii_stats_xls
from fii_engine.nse_fo_bhavcopy import BASE_URL, REPORT_PAGE, _get

REPORTS = {
    "participant_oi": {
        "name": "F&O - Participant wise Open Interest(csv)",
        "extension": ".csv",
        "scope": "participant_category_aggregate_not_stock_specific",
    },
    "participant_volume": {
        "name": "F&O - Participant wise Trading Volumes(csv)",
        "extension": ".csv",
        "scope": "participant_category_aggregate_not_stock_specific",
    },
    "fii_derivatives_statistics": {
        "name": "F&O - FII Derivatives Statistics",
        "extension": ".xls",
        "scope": "aggregate_fii_derivatives_only",
    },
}
# NSE's archive selector metadata is not documented. Try the section/category
# labels represented by the F&O archive UI; only schema/date-valid payloads
# are accepted. The successful pair is written to the manifest.
SELECTOR_CANDIDATES = (
    ("derivatives", "equity-derivatives"),
    ("derivatives", "fo"),
    ("derivatives", "futures-and-options"),
    ("equity-derivatives", "derivatives"),
    ("capital-market", "equity-derivatives"),
)


def build_archive_url(dataset: str, trade_date: date, category: str, section: str) -> str:
    if dataset not in REPORTS:
        raise ValueError(f"unsupported historical F&O dataset: {dataset}")
    archive = [{
        "name": REPORTS[dataset]["name"],
        "type": "daily-reports",
        "category": category,
        "section": section,
    }]
    query = urlencode({
        "archives": json.dumps(archive, separators=(",", ":")),
        "date": trade_date.strftime("%d-%b-%Y"),
        "type": "derivatives",
        "mode": "single",
    })
    return f"{BASE_URL}/api/reports?{query}"


def _unwrap_download(payload: bytes, dataset: str) -> tuple[bytes, str | None]:
    if not payload:
        raise ValueError(f"{dataset}: NSE returned an empty archive response")
    if not payload.startswith(b"PK\x03\x04"):
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
    failures = []
    for category, section in SELECTOR_CANDIDATES:
        url = build_archive_url(dataset, trade_date, category, section)
        try:
            response = _get(url, REPORT_PAGE, "application/octet-stream,*/*")
            extracted, member_name = _unwrap_download(response, dataset)
            inspection = _validate(dataset, extracted, trade_date.isoformat())
            return ({
                "dataset": dataset,
                "trade_date": trade_date.isoformat(),
                "selector_category": category,
                "selector_section": section,
                "request_url": url,
                "archive_member": member_name,
                "raw_bytes": len(response),
                "payload_bytes": len(extracted),
                "archive_sha256": hashlib.sha256(response).hexdigest(),
                "payload_sha256": hashlib.sha256(extracted).hexdigest(),
                "inspection": inspection,
                "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "attribution_scope": REPORTS[dataset].get(
                    "scope", "participant_category_aggregate_not_stock_specific"
                ),
            }, response, extracted)
        except Exception as exc:  # Record each selector failure; do not mask all failures.
            failures.append({
                "category": category,
                "section": section,
                "error": f"{type(exc).__name__}: {exc}",
            })
            time.sleep(0.25)
    raise RuntimeError(json.dumps({
        "dataset": dataset,
        "trade_date": trade_date.isoformat(),
        "selector_attempts": failures,
    }, indent=2))


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
    # A date counts as a completed session only when all three report types
    # validate. Partial dates are retained in the manifest but not counted.
    complete_dates = 0
    # Warm NSE's session before calling the historical archive endpoint.
    _get(REPORT_PAGE, BASE_URL + "/", "text/html,*/*")
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
                    / trade_date.isoformat() / f"{dataset}_{payload_digest[:16]}{spec['extension']}"
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
                    "error": str(exc),
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
        root / "data/raw/nse_fo_historical/manifests" / f"historical_manifest_{digest[:16]}.json",
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
