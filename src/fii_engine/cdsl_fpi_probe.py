"""Probe official CDSL FPI daily, monthly and archive pages without guessing APIs."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

PAGES = {
    "daily": "https://www.cdslindia.com/eservices/publications/fiidaily",
    "monthly": "https://www.cdslindia.com/eservices/publications/FIIMonthly",
    "archive": "https://www.cdslindia.com/Publications/FIITrends.aspx",
    "trade_wise_equity": "https://www.cdslindia.com/Publications/EquityDataFII.html",
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class _SummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.links: list[dict[str, str]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._anchor: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "a":
            self._anchor = {"href": attributes.get("href") or "", "text": ""}

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._cell is not None:
            self._cell.append(text)
        if self._anchor is not None:
            self._anchor["text"] = (self._anchor["text"] + " " + text).strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        elif tag == "a" and self._anchor is not None:
            if self._anchor["href"] or self._anchor["text"]:
                self.links.append(self._anchor)
            self._anchor = None


def summarize_html(payload: bytes) -> dict:
    parser = _SummaryParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    return {
        "table_count": len(parser.tables),
        "tables": [table[:15] for table in parser.tables[:30]],
        "link_count": len(parser.links),
        "links": parser.links[:500],
    }


def _fetch(opener, url: str) -> tuple[bytes, str]:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    for attempt in range(4):
        try:
            with opener.open(request, timeout=45) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP {response.status} for {url}")
                payload = response.read()
                if not payload:
                    raise ValueError(f"empty CDSL page response: {url}")
                return payload, response.headers.get("Content-Type", "")
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"CDSL request failed for {url}: HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"CDSL request failed for {url}: {exc}") from exc
    raise RuntimeError(f"CDSL request failed after retries: {url}")


def probe(output_dir: Path = Path("artifacts/cdsl_probe")) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    results = []
    for name, url in PAGES.items():
        item = {"name": name, "url": url}
        try:
            payload, content_type = _fetch(opener, url)
            digest = hashlib.sha256(payload).hexdigest()
            raw_path = output_dir / f"{name}.html"
            raw_path.write_bytes(payload)
            item.update({
                "status": "success",
                "http_status": 200,
                "content_type": content_type,
                "bytes": len(payload),
                "sha256": digest,
                "raw_path": raw_path.name,
                **summarize_html(payload),
            })
        except Exception as exc:  # Keep independent page outcomes visible.
            item.update({"status": "unavailable", "error": str(exc)})
        results.append(item)
        time.sleep(0.25)

    successful = sum(item["status"] == "success" for item in results)
    manifest = {
        "dataset": "cdsl_fpi_source_discovery",
        "publisher": "Central Depository Services (India) Limited",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if successful == len(PAGES) else "partial",
        "successful_pages": successful,
        "expected_pages": len(PAGES),
        "results": results,
        "scope_note": (
            "Discovery only. HTML table extraction, archive coverage, revisions, "
            "and transaction-level semantics require validation before ingestion."
        ),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if successful == 0:
        raise RuntimeError("all official CDSL source pages were unavailable")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/cdsl_probe"))
    args = parser.parse_args()
    print(json.dumps(probe(args.output_dir), indent=2))


if __name__ == "__main__":
    main()
