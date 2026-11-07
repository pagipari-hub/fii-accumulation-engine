"""Probe official NSE equity shareholding master, schema and RSS disclosures."""
from __future__ import annotations

import argparse
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
PAGE = f"{BASE}/companies-listing/corporate-filings-shareholding-pattern/"
SCHEMA_URL = f"{BASE}/json/CorporateFiling/shareholder/shareholder-equity.json"
MASTER_URL = f"{BASE}/api/corporate-share-holdings-master?index=equities"
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml"
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
                    "content_disposition": response.headers.get("Content-Disposition", ""),
                    "response_url": response.geturl(),
                }
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE shareholding request failed: {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE shareholding request failed: {url}: {exc}") from exc
    raise RuntimeError(f"NSE shareholding request exhausted retries: {url}")


def _json_contract(payload: bytes) -> dict:
    try:
        decoded = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE shareholding endpoint did not return valid JSON") from exc
    result = {
        "top_level_type": type(decoded).__name__,
        "top_level_keys": sorted(decoded) if isinstance(decoded, dict) else [],
    }
    if isinstance(decoded, list):
        result["row_count"] = len(decoded)
        result["sample_rows"] = decoded[:5]
        if decoded and isinstance(decoded[0], dict):
            result["first_row_keys"] = sorted(decoded[0])
    elif isinstance(decoded, dict):
        for key in ("columns", "hoverTable", "data", "records"):
            value = decoded.get(key)
            if isinstance(value, list):
                result[f"{key}_count"] = len(value)
                result[f"{key}_sample"] = value[:3]
                if value and isinstance(value[0], dict):
                    result[f"{key}_first_row_keys"] = sorted(value[0])
    return result


def _rss_contract(payload: bytes) -> dict:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("NSE shareholding RSS feed is not valid XML") from exc
    items = []
    for item in root.findall(".//item"):
        items.append({
            "title": item.findtext("title"),
            "link": item.findtext("link"),
            "pub_date": item.findtext("pubDate"),
            "guid": item.findtext("guid"),
            "description": item.findtext("description"),
        })
    return {
        "root_tag": root.tag,
        "item_count": len(items),
        "sample_items": items[:5],
    }


def _save(output: Path, name: str, payload: bytes, metadata: dict, contract: dict) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    path = output / f"{name}_{digest[:16]}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
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
        raise RuntimeError("NSE shareholding page session bootstrap failed")
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    output_dir.mkdir(parents=True, exist_ok=True)

    schema_payload, schema_meta = _get(SCHEMA_URL, PAGE, "application/json,*/*")
    master_payload, master_meta = _get(MASTER_URL, PAGE, "application/json,*/*")
    rss_payload, rss_meta = _get(RSS_URL, PAGE, "application/rss+xml,application/xml,*/*")
    if not schema_payload or not master_payload or not rss_payload:
        raise ValueError("one or more official NSE shareholding sources returned an empty body")

    observations = [
        _save(output_dir, "shareholding_schema", schema_payload, schema_meta,
              _json_contract(schema_payload)),
        _save(output_dir, "shareholding_master", master_payload, master_meta,
              _json_contract(master_payload)),
        _save(output_dir, "shareholding_rss", rss_payload, rss_meta,
              _rss_contract(rss_payload)),
    ]
    report = {
        "publisher": "National Stock Exchange of India",
        "page_url": PAGE,
        "retrieved_at_utc": retrieved_at,
        "session_bootstrap": {
            "http_status": bootstrap_meta["http_status"],
            "bytes": len(bootstrap),
        },
        "observations": observations,
        "source_boundaries": [
            "The master endpoint is the official equity shareholding-pattern table source.",
            "Quarter-end date and filing/submission date must be kept as separate fields.",
            "The RSS feed is a change-discovery aid, not a complete historical dataset.",
            "FPI classification must come from explicit filing categories/XBRL facts, not client names or inferred labels.",
        ],
    }
    manifest = output_dir / "probe_manifest.json"
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-shareholding-probe"))
    args = parser.parse_args()
    print(json.dumps(probe(args.output_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
