"""Capture and inspect NSE's official F&O Stock Introduction and Exclusion Tracker."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

from openpyxl import load_workbook

SOURCE_PAGE = (
    "https://www.nseindia.com/static/products-services/"
    "equity-derivatives-contract-information"
)
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


class _TrackerLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((" ".join(" ".join(self._text).split()), self._href))
            self._href = None
            self._text = []


def find_tracker_url(html: str, page_url: str = SOURCE_PAGE) -> str:
    parser = _TrackerLinkParser()
    parser.feed(html)
    matches = [
        urljoin(page_url, href)
        for text, href in parser.links
        if "f&o stock introduction and exclusion tracker" in text.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one NSE F&O tracker link; found {len(matches)}")
    url = matches[0]
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARCHIVE_HOST
        or not parsed.path.lower().endswith(".xlsx")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"untrusted NSE F&O tracker URL: {url}")
    return url


def _merged_cell_value(worksheet, row: int, column: int):
    for merged in worksheet.merged_cells.ranges:
        if (
            merged.min_row <= row <= merged.max_row
            and merged.min_col <= column <= merged.max_col
        ):
            return worksheet.cell(merged.min_row, merged.min_col).value
    return worksheet.cell(row, column).value


def _parse_effective_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    for pattern in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized NSE tracker effective date: {value!r}")


def parse_tracker_events(payload: bytes, as_of_date: date) -> dict:
    if not payload.startswith(b"PK"):
        raise ValueError("NSE F&O tracker response is not an XLSX file")
    workbook = load_workbook(BytesIO(payload), read_only=False, data_only=True)
    required_sheets = {"introduction", "exclusion"}
    by_name = {name.casefold(): name for name in workbook.sheetnames}
    if not required_sheets.issubset(by_name):
        raise ValueError(
            f"NSE tracker workbook schema drift: expected Introduction and Exclusion sheets, got {workbook.sheetnames}"
        )
    events = []
    summary = {}
    for action in ("Introduction", "Exclusion"):
        worksheet = workbook[by_name[action.casefold()]]
        expected = ["sr no", "symbol", "security name", "effective date", "circular link"]
        header_row = None
        for candidate in range(1, min(10, worksheet.max_row) + 1):
            headers = [
                str(worksheet.cell(candidate, column).value or "").strip().casefold()
                for column in range(2, 7)
            ]
            if headers == expected:
                header_row = candidate
                break
        if header_row is None:
            raise ValueError(f"{action} sheet header schema drift; expected columns {expected}")
        action_events = []
        for row in range(header_row + 1, worksheet.max_row + 1):
            symbol_value = worksheet.cell(row, 3).value
            name_value = worksheet.cell(row, 4).value
            if not symbol_value or not name_value:
                continue
            symbol = str(symbol_value).strip()
            security_name = str(name_value).strip()
            effective_date = _parse_effective_date(_merged_cell_value(worksheet, row, 5))
            circular_value = _merged_cell_value(worksheet, row, 6)
            circular_links = (
                re.findall(r"https?://[^\s]+", str(circular_value))
                if circular_value else []
            )
            circular_links = [link.rstrip(".,;") for link in circular_links]
            status = (
                "date_unknown" if effective_date is None
                else "effective" if effective_date <= as_of_date
                else "future_scheduled"
            )
            event = {
                "action": action.casefold(),
                "symbol": symbol,
                "security_name": security_name,
                "effective_date": effective_date.isoformat() if effective_date else None,
                "status_as_of_date": status,
                "circular_links": circular_links,
            }
            events.append(event)
            action_events.append(event)
        summary[action.casefold()] = {
            "total": len(action_events),
            "effective_as_of_date": sum(e["status_as_of_date"] == "effective" for e in action_events),
            "future_scheduled": sum(e["status_as_of_date"] == "future_scheduled" for e in action_events),
            "date_unknown": sum(e["status_as_of_date"] == "date_unknown" for e in action_events),
        }
    workbook.close()
    return {
        "as_of_date_ist": as_of_date.isoformat(),
        "summary": summary,
        "events": events,
        "baseline_required": True,
        "note": (
            "The tracker records introductions and exclusions, not a complete starting "
            "universe. Do not publish a current eligible list without a verified baseline."
        ),
    }


def inspect_workbook(payload: bytes) -> dict:
    if not payload.startswith(b"PK"):
        raise ValueError("NSE F&O tracker response is not an XLSX file")
    try:
        workbook = load_workbook(BytesIO(payload), read_only=False, data_only=True)
    except Exception as exc:
        raise ValueError(f"unable to parse NSE F&O tracker workbook: {exc}") from exc
    if not workbook.sheetnames:
        raise ValueError("NSE F&O tracker workbook has no worksheets")
    sheets = []
    for worksheet in workbook.worksheets:
        preview = []
        for row in worksheet.iter_rows(min_row=1, max_row=12, values_only=True):
            values = [None if value is None else str(value).strip() for value in row]
            if any(value not in (None, "") for value in values):
                preview.append(values)
        sheets.append({
            "name": worksheet.title,
            "max_row": worksheet.max_row,
            "max_column": worksheet.max_column,
            "merged_ranges": [str(value) for value in worksheet.merged_cells.ranges],
            "preview_rows": preview,
        })
    workbook.close()
    if not any(sheet["preview_rows"] for sheet in sheets):
        raise ValueError("NSE F&O tracker workbook contains no visible cell data")
    return {"sheet_names": [sheet["name"] for sheet in sheets], "sheets": sheets}


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


def capture(output_dir: Path = Path("artifacts/nse-fo-tracker"), repo_root: Path | None = None) -> dict:
    page = _get(SOURCE_PAGE, "https://www.nseindia.com/", "text/html,*/*")
    tracker_url = find_tracker_url(page.decode("utf-8", errors="replace"))
    payload = _get(
        tracker_url,
        SOURCE_PAGE,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
    )
    if not payload:
        raise ValueError("NSE F&O tracker download is empty")
    digest = hashlib.sha256(payload).hexdigest()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"fo_stock_introduction_exclusion_{digest[:16]}.xlsx"
    raw_path.write_bytes(payload)
    as_of_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    parse_error = None
    try:
        workbook_info = inspect_workbook(payload)
        event_history = parse_tracker_events(payload, as_of_date)
        interpretation_status = "effective_dated_change_history_parsed_baseline_required"
    except Exception as exc:
        workbook_info = {"parse_error": str(exc)}
        event_history = None
        interpretation_status = "schema_parse_failed_raw_preserved"
        parse_error = str(exc)
    report = {
        "publisher": "National Stock Exchange of India",
        "source_page": SOURCE_PAGE,
        "tracker_url": tracker_url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bytes": len(payload),
        "sha256": digest,
        "raw_artifact_path": str(raw_path),
        "workbook": workbook_info,
        "event_history": event_history,
        "interpretation_status": interpretation_status,
        "parse_error": parse_error,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if repo_root is not None:
        persistent_raw_dir = repo_root / "data/raw/fno_tracker"
        persistent_snapshot_dir = repo_root / "data/snapshots/fno_tracker"
        persistent_raw_dir.mkdir(parents=True, exist_ok=True)
        persistent_snapshot_dir.mkdir(parents=True, exist_ok=True)
        persistent_raw = persistent_raw_dir / f"fno_tracker_source_{digest[:16]}.xlsx"
        current_raw = persistent_raw_dir / "fno_tracker_current.xlsx"
        if persistent_raw.exists() and hashlib.sha256(persistent_raw.read_bytes()).hexdigest() != digest:
            raise RuntimeError("immutable F&O tracker source path collision")
        if not persistent_raw.exists():
            persistent_raw.write_bytes(payload)
        current_raw.write_bytes(payload)
        persistent_manifest = persistent_snapshot_dir / f"fno_tracker_{digest[:16]}.json"
        if not persistent_manifest.exists():
            persistent_record = {
                "publisher": report["publisher"],
                "source_page": report["source_page"],
                "tracker_url": report["tracker_url"],
                "retrieved_at_utc": report["retrieved_at_utc"],
                "bytes": report["bytes"],
                "sha256": digest,
                "raw_file": str(persistent_raw),
                "workbook": report["workbook"],
                "schema_version": 1,
            }
            persistent_manifest.write_text(
                json.dumps(persistent_record, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        report["persistent_raw_path"] = str(persistent_raw)
        report["persistent_current_path"] = str(current_raw)
        report["persistent_manifest_path"] = str(persistent_manifest)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-fo-tracker"))
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(capture(args.output_dir, args.repo_root), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
