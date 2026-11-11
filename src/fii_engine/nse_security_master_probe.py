"""Probe and archive official NSE equity identity reference files.

The current equity list and change registers are discovery evidence only. They
must not be treated as historical ISIN proof without dated issuer/exchange
evidence covering the reporting date.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://www.nseindia.com"
PAGE = f"{BASE}/static/market-data/securities-available-for-trading"
SOURCES = {
    "equity_master": "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "company_name_changes": "https://nsearchives.nseindia.com/content/equities/namechange.csv",
    "symbol_changes": "https://nsearchives.nseindia.com/content/equities/symbolchange.csv",
}
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))


def _get(url: str, referer: str, accept: str, timeout: int = 60) -> tuple[bytes, dict]:
    request = Request(url, headers={**HEADERS, "Referer": referer, "Accept": accept})
    for attempt in range(4):
        try:
            with HTTP.open(request, timeout=timeout) as response:
                payload = response.read()
                return payload, {
                    "http_status": response.status,
                    "content_type": response.headers.get("Content-Type", ""),
                    "response_url": response.geturl(),
                }
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE identity source request failed: {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE identity source request failed: {url}: {exc}") from exc
    raise RuntimeError(f"NSE identity source retries exhausted: {url}")


def inspect_csv(payload: bytes, *, source_name: str) -> dict:
    if not payload:
        raise ValueError(f"{source_name}: empty response")
    text = payload.decode("utf-8-sig")
    if text.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise ValueError(f"{source_name}: received HTML instead of CSV")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"{source_name}: CSV header missing")
    headers = [str(value or "").strip() for value in reader.fieldnames]
    if any(not value for value in headers) or len(set(headers)) != len(headers):
        raise ValueError(f"{source_name}: blank or duplicate CSV headers")
    rows = list(reader)
    if not rows:
        raise ValueError(f"{source_name}: CSV has no data rows")
    return {
        "header": headers,
        "row_count": len(rows),
        "sample_rows": rows[:5],
    }


def _save(output: Path, name: str, payload: bytes, metadata: dict) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    path = output / f"{name}_{digest[:16]}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"immutable NSE source collision: {path}")
    if not path.exists():
        path.write_bytes(payload)
    contract = inspect_csv(payload, source_name=name)
    return {
        "name": name,
        "url": metadata["response_url"],
        **metadata,
        "bytes": len(payload),
        "sha256": digest,
        "saved_path": str(path),
        "contract": contract,
    }


def probe(output_dir: Path) -> dict:
    bootstrap, bootstrap_meta = _get(PAGE, BASE + "/", "text/html,*/*")
    if not bootstrap or bootstrap_meta["http_status"] != 200:
        raise RuntimeError("NSE securities page session bootstrap failed")
    observations = []
    for name, url in SOURCES.items():
        payload, metadata = _get(url, PAGE, "text/csv,text/plain,*/*")
        observations.append(_save(output_dir, name, payload, metadata))

    report = {
        "publisher": "National Stock Exchange of India",
        "page_url": PAGE,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "session_bootstrap": {"http_status": bootstrap_meta["http_status"], "bytes": len(bootstrap)},
        "observations": observations,
        "identity_safety": {
            "current_equity_master_is_current_state_only": True,
            "change_registers_are_discovery_evidence_until_effective_dates_and_identity_are_verified": True,
            "does_not_authorize_historical_isin_override": True,
            "reporting_date_must_be_covered_by_effective_dated_official_evidence": True,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "probe_manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-security-master-probe"))
    args = parser.parse_args()
    print(json.dumps(probe(args.output_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
