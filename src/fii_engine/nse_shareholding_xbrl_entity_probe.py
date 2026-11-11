"""Probe NSE shareholding XBRL entity identifiers for selected ISIN-mismatch filings.

The probe captures issuer entity identifiers from XBRL contexts. It does not
equate an identifier to a company without an independent issuer registry
crosswalk.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://www.nseindia.com"
BOOTSTRAP_PAGE = f"{BASE}/static/market-data/securities-available-for-trading"
PAGE = f"{BASE}/companies-listing/corporate-filings-shareholding-pattern/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
HTTP = build_opener(HTTPCookieProcessor(CookieJar()))


def parse_xbrl_entity(payload: bytes) -> dict:
    if not payload:
        raise ValueError("empty XBRL payload")
    if payload.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError("received HTML instead of XBRL")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("invalid XBRL XML") from exc
    identifiers, name_facts = [], []
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1].lower()
        value = (element.text or "").strip()
        if local == "identifier" and value:
            identifiers.append({"scheme": element.attrib.get("scheme", ""), "value": value})
        if value and local in {"nameofcompany", "nameofentity", "entityname", "companyname", "nameofreportingentity"}:
            name_facts.append({"tag": element.tag.rsplit("}", 1)[-1], "value": value})
    unique_ids, seen = [], set()
    for item in identifiers:
        key = (item["scheme"], item["value"])
        if key not in seen:
            seen.add(key)
            unique_ids.append(item)
    unique_names, seen_names = [], set()
    for item in name_facts:
        key = (item["tag"], item["value"])
        if key not in seen_names:
            seen_names.add(key)
            unique_names.append(item)
    if not unique_ids:
        raise ValueError("XBRL contains no entity identifier in contexts")
    return {"entity_identifiers": unique_ids, "company_name_facts": unique_names, "identifier_count": len(unique_ids)}


def _get(url: str, referer: str, accept: str = "*/*") -> tuple[bytes, dict]:
    request = Request(url, headers={
        "User-Agent": UA, "Referer": referer, "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    })
    for attempt in range(4):
        try:
            with HTTP.open(request, timeout=60) as response:
                return response.read(), {
                    "http_status": response.status,
                    "content_type": response.headers.get("Content-Type", ""),
                    "response_url": response.geturl(),
                }
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE XBRL request failed for {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE XBRL request failed for {url}: {exc}") from exc
    raise RuntimeError(f"NSE XBRL retries exhausted for {url}")


def probe(candidates_csv: Path, output_dir: Path, symbols: list[str]) -> dict:
    with candidates_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_symbol = {row["symbol"]: row for row in rows}
    missing = [symbol for symbol in symbols if symbol not in by_symbol]
    if missing:
        raise ValueError(f"requested symbols missing from candidate audit: {missing}")
    bootstrap, meta = _get(BOOTSTRAP_PAGE, BASE + "/", "text/html,*/*")
    if meta["http_status"] != 200 or not bootstrap:
        raise RuntimeError("NSE session bootstrap failed")
    output_dir.mkdir(parents=True, exist_ok=True)
    observations = []
    for symbol in symbols:
        candidate = by_symbol[symbol]
        url = candidate["master_xbrl_url"]
        payload, response = _get(url, PAGE, "application/xml,text/xml,*/*")
        parsed = parse_xbrl_entity(payload)
        digest = hashlib.sha256(payload).hexdigest()
        raw_path = output_dir / f"shareholding_xbrl_{symbol}_{digest[:16]}.xml"
        if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"immutable XBRL payload collision: {raw_path}")
        if not raw_path.exists():
            raw_path.write_bytes(payload)
        observations.append({
            "symbol": symbol, "reporting_date": candidate["reporting_date"],
            "universe_isin": candidate["universe_isin"],
            "shareholding_master_isin": candidate["master_isin"],
            "master_company_name": candidate["master_company_name"],
            "master_record_id": candidate["master_record_id"], "xbrl_url": url,
            **response, "bytes": len(payload), "sha256": digest,
            "raw_path": str(raw_path), **parsed,
            "identity_boundary": "Entity identifier extracted from filing only; no CIN/company registry crosswalk or ISIN override performed.",
        })
    report = {
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "session_bootstrap": {"http_status": meta["http_status"], "bytes": len(bootstrap)},
        "symbols_probed": symbols, "observations": observations,
        "safety": {
            "entity_identifiers_are_not_assumed_to_be_cin_without_scheme_validation": True,
            "no_ownership_identity_status_changed": True, "no_isin_override_performed": True,
        },
    }
    (output_dir / "shareholding_xbrl_entity_probe.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", default=["360ONE", "AARTIIND", "ABB"])
    args = parser.parse_args()
    print(json.dumps(probe(args.candidates, args.output_dir, args.symbols), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
