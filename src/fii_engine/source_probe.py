"""Inspect official source pages and discover linked resources and JS endpoints.

This is a discovery/health-check stage, not a market-data parser. It deliberately
does not guess file schemas or treat a source page response as a data observation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener

SOURCE_PAGES = {
    "nse_nifty500": "https://www.nseindia.com/static/products-services/indices-nifty500-index",
    "nse_fii_dii": "https://www.nseindia.com/reports/fii-dii",
    "nse_equities_reports": "https://www.nseindia.com/all-reports",
    "nse_bulk_block_deals": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
    "nse_derivatives": "https://www.nseindia.com/all-reports-derivatives",
    "nse_fo_contract_information": "https://www.nseindia.com/static/products-services/equity-derivatives-contract-information",
    "nse_fo_individual_securities": "https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities",
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))
SESSION_BOOTSTRAP = "https://www.nseindia.com/"

DOWNLOAD_SUFFIXES = (".csv", ".zip", ".xls", ".xlsx", ".pdf", ".gz", ".parquet")
DOWNLOAD_WORDS = re.compile(r"download|bhavcopy|participant|contract|constituent|report", re.I)
ENDPOINT_WORDS = re.compile(
    r"api|report|download|market|deriv|bhav|participant|historical|fo_eq|fii|dii",
    re.I,
)
URL_OR_PATH = re.compile(r"""(?P<value>(?:https?:(?:/|\\/){2}|/)[^"'\s<>]{2,240})""")
MAX_SCRIPT_COUNT = 20
MAX_SCRIPT_BYTES = 1_000_000


@dataclass(frozen=True)
class Link:
    text: str
    href: str


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[Link] = []
        self.script_urls: list[str] = []
        self.inline_scripts: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._inside_script = False
        self._script_src: str | None = None
        self._script_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "a":
            self._href = attributes.get("href")
            self._text = []
        elif tag.lower() == "script":
            self._inside_script = True
            self._script_src = attributes.get("src")
            self._script_text = []
            if self._script_src:
                self.script_urls.append(self._script_src)

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)
        if self._inside_script and self._script_src is None:
            self._script_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(Link(" ".join(" ".join(self._text).split()), self._href))
            self._href = None
            self._text = []
        elif tag.lower() == "script" and self._inside_script:
            if self._script_src is None and self._script_text:
                self.inline_scripts.append("\n".join(self._script_text))
            self._inside_script = False
            self._script_src = None
            self._script_text = []


def parse_html(html: str) -> AnchorParser:
    parser = AnchorParser()
    parser.feed(html)
    return parser


def discover_links(html: str) -> list[dict[str, str]]:
    """Return likely download links from HTML; preserve the original href."""
    parser = parse_html(html)
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


def discover_script_urls(html: str, page_url: str) -> list[str]:
    """Resolve same-origin script assets only; do not fetch third-party scripts."""
    parser = parse_html(html)
    page_origin = urlparse(page_url).netloc.lower()
    found: list[str] = []
    seen: set[str] = set()
    for raw_url in parser.script_urls:
        absolute = urljoin(page_url, raw_url)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != page_origin:
            continue
        if absolute not in seen:
            found.append(absolute)
            seen.add(absolute)
    return found[:MAX_SCRIPT_COUNT]


def extract_endpoint_candidates(text: str) -> list[str]:
    """Extract endpoint-like URL/path strings for inspection, not execution."""
    candidates: list[str] = []
    seen: set[str] = set()
    for match in URL_OR_PATH.finditer(text):
        value = match.group("value").replace("\\/", "/")
        if not ENDPOINT_WORDS.search(value):
            continue
        value = value.rstrip("),;]")
        if value not in seen:
            candidates.append(value)
            seen.add(value)
    return candidates


def extract_endpoint_contexts(text: str, radius: int = 320, limit: int = 20) -> list[dict[str, object]]:
    """Capture bounded source context around discovered API paths for human review.

    Context is evidence for endpoint discovery only; it is never executed.
    """
    contexts: list[dict[str, object]] = []
    pattern = re.compile(r"/api/[A-Za-z0-9_./-]+(?:\\?[^\\s\"'<>]{0,100})?")
    for match in pattern.finditer(text):
        value = match.group(0)
        if not ENDPOINT_WORDS.search(value):
            continue
        start, end = max(0, match.start() - radius), min(len(text), match.end() + radius)
        contexts.append({
            "endpoint": value,
            "context_start": start,
            "context": text[start:end],
        })
        if len(contexts) >= limit:
            break
    return contexts


def inspect_script(url: str, page_url: str, timeout: int = 15) -> dict[str, object]:
    checked_at = datetime.now(timezone.utc).isoformat()
    request = Request(
        url,
        headers={
            **REQUEST_HEADERS,
            "Accept": "application/javascript,text/javascript,*/*",
            "Referer": page_url,
        },
    )
    try:
        with HTTP.open(request, timeout=timeout) as response:
            body = response.read(MAX_SCRIPT_BYTES + 1)
            truncated = len(body) > MAX_SCRIPT_BYTES
            body = body[:MAX_SCRIPT_BYTES]
            text_body = body.decode("utf-8", errors="replace")
            return {
                "url": url,
                "checked_at_utc": checked_at,
                "http_status": response.status,
                "content_type": response.headers.get("Content-Type", ""),
                "bytes_inspected": len(body),
                "truncated": truncated,
                "sha256_prefix": hashlib.sha256(body).hexdigest(),
                "endpoint_candidates": extract_endpoint_candidates(text_body),
                "endpoint_contexts": extract_endpoint_contexts(text_body, radius=1600, limit=50),
                "status": "ok" if response.status == 200 else "http_error",
            }
    except HTTPError as exc:
        return {"url": url, "checked_at_utc": checked_at,
                "http_status": exc.code, "status": "http_error", "error": str(exc)}
    except (URLError, TimeoutError, OSError) as exc:
        return {"url": url, "checked_at_utc": checked_at,
                "status": "connection_error", "error": str(exc)}


def inspect_page(name: str, url: str, timeout: int = 30) -> dict[str, object]:
    request = Request(url, headers={**REQUEST_HEADERS, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Referer": SESSION_BOOTSTRAP})
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        with HTTP.open(request, timeout=timeout) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            html = body.decode("utf-8", errors="replace")
            parser = parse_html(html)
            scripts = [
                inspect_script(script_url, url)
                for script_url in discover_script_urls(html, url)
            ]
            inline_candidates = []
            for inline_script in parser.inline_scripts:
                inline_candidates.extend(extract_endpoint_candidates(inline_script))
            all_candidates = []
            seen_candidates: set[str] = set()
            for candidate in inline_candidates + [
                endpoint
                for script in scripts
                for endpoint in script.get("endpoint_candidates", [])
            ]:
                if candidate not in seen_candidates:
                    all_candidates.append(candidate)
                    seen_candidates.add(candidate)
            return {
                "source": name,
                "page_url": url,
                "checked_at_utc": checked_at,
                "http_status": response.status,
                "content_type": content_type,
                "page_sha256": hashlib.sha256(body).hexdigest(),
                "page_bytes": len(body),
                "candidate_download_links": discover_links(html),
                "same_origin_script_count": len(scripts),
                "script_inspections": scripts,
                "endpoint_candidates": all_candidates,
                "status": "ok" if response.status == 200 else "http_error",
            }
    except HTTPError as exc:
        return {"source": name, "page_url": url, "checked_at_utc": checked_at,
                "http_status": exc.code, "status": "http_error", "error": str(exc)}
    except (URLError, TimeoutError, OSError) as exc:
        return {"source": name, "page_url": url, "checked_at_utc": checked_at,
                "status": "connection_error", "error": str(exc)}


def run_probe(output: Path) -> dict[str, object]:
    # Reuse cookies issued by NSE's landing page for all source requests.
    bootstrap = {"url": SESSION_BOOTSTRAP, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        request = Request(SESSION_BOOTSTRAP, headers={**REQUEST_HEADERS, "Accept": "text/html,*/*"})
        with HTTP.open(request, timeout=20) as response:
            bootstrap.update({"status": "ok" if response.status == 200 else "http_error",
                              "http_status": response.status})
            response.read(65536)
    except HTTPError as exc:
        bootstrap.update({"status": "http_error", "http_status": exc.code, "error": str(exc)})
    except (URLError, TimeoutError, OSError) as exc:
        bootstrap.update({"status": "connection_error", "error": str(exc)})

    results = [inspect_page(name, url) for name, url in SOURCE_PAGES.items()]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "session_bootstrap": bootstrap,
        "purpose": (
            "source-page and same-origin JavaScript discovery only; "
            "no market data has been ingested and endpoint candidates are not executed"
        ),
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
