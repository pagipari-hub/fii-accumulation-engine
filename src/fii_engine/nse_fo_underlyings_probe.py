"""Probe NSE's current List of Underlyings and Information page."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

SOURCE_PAGE = "https://www.nseindia.com/products-services/equity-derivatives-list-underlyings-information"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def parse_underlying_tables(html: str) -> dict:
    parser = _TableParser()
    parser.feed(html)
    tables = []
    current = None
    for row in parser.rows:
        normalized = [cell.strip() for cell in row]
        lowered = [cell.casefold() for cell in normalized]
        is_header = "underlying" in lowered and "symbol" in lowered
        if is_header:
            if current:
                tables.append(current)
            current = {"header": normalized, "rows": []}
        elif current and len(normalized) >= 3:
            current["rows"].append(normalized)
    if current:
        tables.append(current)

    for table in tables:
        table["data_rows"] = [
            row for row in table["rows"]
            if row and re.fullmatch(r"\d+", row[0].replace(".", "").strip())
            and row[-1].strip()
        ]
        table["symbol_count"] = len(table["data_rows"])
    return {
        "html_table_count": len(parser.rows),
        "underlying_tables": tables,
        "total_underlying_rows": sum(table["symbol_count"] for table in tables),
    }


def capture(output_dir: Path = Path("artifacts/nse-fo-underlyings")) -> dict:
    request = Request(
        SOURCE_PAGE,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with HTTP.open(request, timeout=45) as response:
            if response.status != 200:
                raise RuntimeError(f"NSE underlyings page returned HTTP {response.status}")
            html_bytes = response.read()
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"NSE underlyings page request failed: {exc}") from exc

    html = html_bytes.decode("utf-8", errors="replace")
    parsed = parse_underlying_tables(html)
    digest = hashlib.sha256(html_bytes).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"nse_fo_underlyings_{digest[:16]}.html"
    raw_path.write_bytes(html_bytes)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    report = {
        "publisher": "National Stock Exchange of India",
        "source_page": SOURCE_PAGE,
        "final_url": final_url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "http_status": 200,
        "content_type": content_type,
        "html_bytes": len(html_bytes),
        "html_sha256": digest,
        "title": " ".join(title_match.group(1).split()) if title_match else None,
        "raw_html_artifact_path": str(raw_path),
        "parsed": parsed,
        "interpretation_status": (
            "html_tables_found_pending_symbol_validation"
            if parsed["total_underlying_rows"]
            else "page_captured_but_underlying_table_not_server_rendered"
        ),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-fo-underlyings"))
    args = parser.parse_args()
    print(json.dumps(capture(args.output_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
