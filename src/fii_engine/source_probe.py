"""Inspect official source pages and discover linked downloadable resources.

This is a discovery/health-check stage, not a market-data parser. It deliberately
does not guess file schemas or treat a source page response as a data observation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCE_PAGES = {
    "nse_nifty500": "https://www.nseindia.com/static/products-services/indices-nifty500-index",
    "nse_fii_dii": "https://www.nseindia.com/reports/fii-dii",
    "nse_derivatives": "https://www.nseindia.com/all-reports-derivatives",
}
USER_AGENT = (
    "Mozilla/5.0 (compatible; FIIAccumulationResearch/0.1; "
    "+https://github.com/pagipari-hub/fii-accumulation-engine)"
)
DOWNLOAD_SUFFIXES = (".csv", ".zip", ".xls", ".xlsx", ".pdf", ".gz", ".parquet")
DOWNLOAD_WORDS = re.compile(r"download|bhavcopy|participant|contract|constituent|report", re.I)


@dataclass(frozen=True)
class Link:
    text: str
    href: str


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[Link] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(Link(" ".join(" ".join(self._text).split()), self._href))
            self._href = None
            self._text = []


def discover_links(html: str) -> list[dict[str, str]]:
    """Return likely download links from HTML; preserve the original href."""
    parser = AnchorParser()
    parser.feed(html)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        href = link.href.strip()
        candidate = href.lower().split("?", 1)[0].endswith(DOWNLOAD_SUFFIXES)
        candidate = candidate or bool(DOWNLOAD_WORDS.search(link.text))
        if href and candidate and href not in seen:
            found.append({"text": link.text, "href": href})
            seen.add(href)
    return found


def inspect_page(name: str, url: str, timeout: int = 30) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            html = body.decode("utf-8", errors="replace")
            return {
                "source": name,
                "page_url": url,
                "checked_at_utc": checked_at,
                "http_status": response.status,
                "content_type": content_type,
                "page_sha256": hashlib.sha256(body).hexdigest(),
                "page_bytes": len(body),
                "candidate_download_links": discover_links(html),
                "status": "ok" if response.status == 200 else "http_error",
            }
    except HTTPError as exc:
        return {"source": name, "page_url": url, "checked_at_utc": checked_at,
                "http_status": exc.code, "status": "http_error", "error": str(exc)}
    except (URLError, TimeoutError, OSError) as exc:
        return {"source": name, "page_url": url, "checked_at_utc": checked_at,
                "status": "connection_error", "error": str(exc)}


def run_probe(output: Path) -> dict[str, object]:
    results = [inspect_page(name, url) for name, url in SOURCE_PAGES.items()]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "source-page discovery only; no market data has been ingested",
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/source-probe/report.json"))
    args = parser.parse_args()
    result = run_probe(args.output)
    for item in result["results"]:
        print(f'{item["source"]}: {item["status"]} ({item.get("http_status", "no HTTP response")})')
