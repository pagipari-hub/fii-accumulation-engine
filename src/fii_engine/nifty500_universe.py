"""Official NSE Indices Nifty 500 constituent snapshot ingestion."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE_PAGE = "https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-500"
CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": SOURCE_PAGE,
    "Cache-Control": "no-cache",
}
MIN_CONSTITUENTS, MAX_CONSTITUENTS = 480, 520
COLUMNS = ("symbol", "company_name", "industry", "series", "isin")
FRESHNESS_NOTE = "Data as on last trading day of the previous month"


def parse_page_freshness(html: str) -> str:
    """Require the official page's stated freshness semantics; don't invent an as-of date."""
    plain = re.sub(r"<[^>]+>", " ", html)
    plain = " ".join(plain.split())
    if FRESHNESS_NOTE.lower() not in plain.lower():
        raise ValueError("official Nifty Indices page freshness statement changed or is missing")
    return FRESHNESS_NOTE


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def parse_constituent_csv(payload: bytes) -> list[dict[str, str]]:
    rows = list(csv.reader(payload.decode("utf-8-sig").splitlines()))
    header_at, headers = None, []
    for i, row in enumerate(rows):
        candidate = [_header(v) for v in row]
        if {"company_name", "industry", "symbol", "series", "isin_code"}.issubset(candidate):
            header_at, headers = i, candidate
            break
    if header_at is None:
        raise ValueError("NSE Indices CSV schema drift: required constituent header not found")
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
    if not MIN_CONSTITUENTS <= len(result) <= MAX_CONSTITUENTS:
        raise ValueError(
            f"constituent count {len(result)} outside safety range "
            f"{MIN_CONSTITUENTS}-{MAX_CONSTITUENTS}"
        )
    symbols, isins = [r["symbol"] for r in result], [r["isin"] for r in result]
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


def _get(url: str, accept: str) -> bytes:
    request = Request(url, headers={**HEADERS, "Accept": accept})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected HTTP status {response.status}: {url}")
        return response.read()


def ingest(root: Path = Path(".")) -> dict[str, object]:
    page = _get(SOURCE_PAGE, "text/html,*/*").decode("utf-8", "replace")
    freshness = parse_page_freshness(page)
    raw = _get(CSV_URL, "text/csv,application/csv,application/octet-stream,*/*")
    records = parse_constituent_csv(raw)
    normalized = normalized_csv(records)
    raw_hash = hashlib.sha256(raw).hexdigest()
    norm_hash = hashlib.sha256(normalized).hexdigest()
    retrieved_at = datetime.now(timezone.utc).isoformat()

    raw_dir, snap_dir = root / "data/raw/universe", root / "data/snapshots/universe"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"nifty500_source_{raw_hash[:16]}.csv"
    snapshot = snap_dir / f"nifty500_{norm_hash[:16]}.csv"
    manifest_path = snap_dir / f"nifty500_{norm_hash[:16]}.json"
    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_hash:
        raise RuntimeError("immutable raw-source path collision")
    if snapshot.exists() and hashlib.sha256(snapshot.read_bytes()).hexdigest() != norm_hash:
        raise RuntimeError("immutable normalized snapshot hash mismatch")
    if not raw_path.exists():
        raw_path.write_bytes(raw)
    if not snapshot.exists():
        snapshot.write_bytes(normalized)
    if not manifest_path.exists():
        manifest = {
            "dataset": "nifty500_constituents", "publisher": "NSE Indices",
            "source_page": SOURCE_PAGE, "source_download_url": CSV_URL,
            "source_freshness_statement": freshness,
            "source_freshness_semantics": "monthly page statement; exact constituent effective date not supplied here",
            "retrieved_at_utc": retrieved_at,
            "raw_sha256": raw_hash, "normalized_sha256": norm_hash,
            "constituent_count": len(records), "accepted_count_range": [MIN_CONSTITUENTS, MAX_CONSTITUENTS],
            "symbols_unique": True, "isins_unique": True, "schema_version": 1,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    current = raw_dir / "nifty500_current.csv"
    current.write_bytes(normalized)
    return {"retrieved_at_utc": retrieved_at, "constituent_count": len(records),
            "source_freshness_statement": freshness, "raw_sha256": raw_hash,
            "normalized_sha256": norm_hash, "raw_path": str(raw_path),
            "snapshot_path": str(snapshot), "manifest_path": str(manifest_path),
            "current_path": str(current)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
