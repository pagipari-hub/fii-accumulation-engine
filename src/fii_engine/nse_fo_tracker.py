"""Capture and inspect NSE's official F&O Stock Introduction and Exclusion Tracker."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
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


def inspect_workbook(payload: bytes) -> dict:
    if not payload.startswith(b"PK"):
        raise ValueError("NSE F&O tracker response is not an XLSX file")
    try:
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True)
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


def capture(output_dir: Path = Path("artifacts/nse-fo-tracker")) -> dict:
    page = _get(SOURCE_PAGE, "https://www.nseindia.com/", "text/html,*/*")
    tracker_url = find_tracker_url(page.decode("utf-8", errors="replace"))
    payload = _get(
        tracker_url,
        SOURCE_PAGE,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
    )
    if not payload:
        raise ValueError("NSE F&O tracker download is empty")
    workbook_info = inspect_workbook(payload)
    digest = hashlib.sha256(payload).hexdigest()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"fo_stock_introduction_exclusion_{digest[:16]}.xlsx"
    raw_path.write_bytes(payload)
    report = {
        "publisher": "National Stock Exchange of India",
        "source_page": SOURCE_PAGE,
        "tracker_url": tracker_url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bytes": len(payload),
        "sha256": digest,
        "raw_artifact_path": str(raw_path),
        "workbook": workbook_info,
        "interpretation_status": "schema_inspected_eligibility_not_yet_derived",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-fo-tracker"))
    args = parser.parse_args()
    print(json.dumps(capture(args.output_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
