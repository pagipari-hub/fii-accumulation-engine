"""Official NSE Nifty 500 constituent snapshot ingestion."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

SOURCE_PAGE = "https://www.nseindia.com/static/products-services/indices-nifty500-index"
BOOTSTRAP = "https://www.nseindia.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
COLUMNS = ("symbol", "company_name", "industry", "series", "isin")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            self.links.append((" ".join(" ".join(self.text).split()), self.href))
            self.href, self.text = None, []


def discover_csv_link(html: str) -> str:
    parser = _Links()
    parser.feed(html)
    matches = [(t, h) for t, h in parser.links
               if "download list of nifty 500 stocks" in t.lower() and ".csv" in t.lower()]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one official Nifty 500 CSV link; found {len(matches)}")
    url = urljoin(SOURCE_PAGE, matches[0][1])
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "nsearchives.nseindia.com":
        raise ValueError(f"unexpected constituent download host or scheme: {url}")
    return url


def parse_page_updated_on(html: str) -> str:
    match = re.search(r"Updated\s+on\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", html, re.I)
    if not match:
        raise ValueError("official NSE page did not expose its 'Updated on' date")
    return datetime.strptime(match.group(1), "%d/%m/%Y").date().isoformat()


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_constituent_csv(payload: bytes) -> list[dict[str, str]]:
    rows = list(csv.reader(payload.decode("utf-8-sig").splitlines()))
    header_at = None
    headers = []
    for i, row in enumerate(rows):
        candidate = [_header(v) for v in row]
        if {"company_name", "industry", "symbol", "series", "isin_code"}.issubset(candidate):
            header_at, headers = i, candidate
            break
    if header_at is None:
        raise ValueError("NSE CSV schema drift: required constituent header not found")
    indexes = {key: headers.index(src) for key, src in {
        "company_name": "company_name", "industry": "industry", "symbol": "symbol",
        "series": "series", "isin": "isin_code",
    }.items()}
    result = []
    for row in rows[header_at + 1:]:
        if not row or not any(v.strip() for v in row):
            continue
        if len(row) < len(headers):
            raise ValueError("truncated constituent row")
        item = {key: row[i].strip() for key, i in indexes.items()}
        if any(not item[k] for k in COLUMNS):
            raise ValueError(f"blank required field for symbol {item['symbol']!r}")
        result.append(item)
    if len(result) != 500:
        raise ValueError(f"expected 500 constituents, received {len(result)}")
    symbols = [r["symbol"] for r in result]
    isins = [r["isin"] for r in result]
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate NSE symbols in constituent list")
    if len(set(isins)) != len(isins):
        raise ValueError("duplicate ISINs in constituent list")
    return sorted(result, key=lambda r: r["symbol"])


def normalized_csv(records: list[dict[str, str]]) -> bytes:
    from io import StringIO
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode()


def _get(opener, url: str, referer: str, accept: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={**HEADERS, "Accept": accept, "Referer": referer})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status {response.status}: {url}")
        return response.read()


def ingest(root: Path = Path(".")) -> dict[str, object]:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    _get(opener, BOOTSTRAP, BOOTSTRAP, "text/html,*/*", 20)
    page = _get(opener, SOURCE_PAGE, BOOTSTRAP, "text/html,*/*").decode("utf-8", "replace")
    page_date = parse_page_updated_on(page)
    csv_url = discover_csv_link(page)
    raw = _get(opener, csv_url, SOURCE_PAGE, "text/csv,application/csv,*/*")
    records = parse_constituent_csv(raw)
    normalized = normalized_csv(records)
    raw_hash = hashlib.sha256(raw).hexdigest()
    norm_hash = hashlib.sha256(normalized).hexdigest()
    stamp = page_date.replace("-", "")
    raw_dir, snap_dir = root / "data/raw/universe", root / "data/snapshots/universe"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"nifty500_source_{stamp}_{raw_hash[:12]}.csv"
    snapshot = snap_dir / f"nifty500_{stamp}.csv"
    manifest_path = snap_dir / f"nifty500_{stamp}.json"
    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_hash:
        raise RuntimeError("immutable raw-source path collision")
    if snapshot.exists():
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != norm_hash:
            raise RuntimeError("constituent content changed without a new page update date; refusing overwrite")
    else:
        snapshot.write_bytes(normalized)
    if not raw_path.exists():
        raw_path.write_bytes(raw)
    if not manifest_path.exists():
        manifest = {
            "dataset": "nifty500_constituents", "publisher": "NSE Indices",
            "source_page": SOURCE_PAGE, "source_download_url": csv_url,
            "source_page_updated_on": page_date,
            "source_page_updated_on_semantics": "page metadata only; not a membership effective date",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_sha256": raw_hash, "normalized_sha256": norm_hash,
            "constituent_count": len(records), "symbols_unique": True,
            "isins_unique": True, "schema_version": 1,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    current = raw_dir / "nifty500_current.csv"
    current.write_bytes(normalized)
    return {"source_page_updated_on": page_date, "constituent_count": len(records),
            "raw_sha256": raw_hash, "normalized_sha256": norm_hash,
            "raw_path": str(raw_path), "snapshot_path": str(snapshot),
            "manifest_path": str(manifest_path), "current_path": str(current)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
