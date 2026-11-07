"""Probe the official NSE bulk/block-deal API contract without publishing market data."""
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
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://www.nseindia.com"
PAGE = f"{BASE}/report-detail/display-bulk-and-block-deals"
ENDPOINT = f"{BASE}/api/historicalOR/bulk-block-short-deals"
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
# This is the exact date range shown by the official NSE historical-deals page
# during the 7 Nov 2026 source inspection; not an inferred archive boundary.
PAGE_DISPLAYED_FROM = "30-10-2026"
PAGE_DISPLAYED_TO = "06-11-2026"
OPTION_TYPES = ("bulk_deals", "block_deals")


def _get(url: str, referer: str, accept: str, timeout: int = 45) -> tuple[bytes, dict]:
    request = Request(
        url,
        headers={**HEADERS, "Referer": referer, "Accept": accept},
    )
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
            raise RuntimeError(f"NSE deals request failed: {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE deals request failed: {url}: {exc}") from exc
    raise RuntimeError(f"NSE deals request exhausted retries: {url}")


def _json_contract(payload: bytes) -> dict:
    try:
        decoded = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"json_valid": False, "top_level_type": "non_json"}
    info = {
        "json_valid": True,
        "top_level_type": type(decoded).__name__,
        "top_level_keys": sorted(decoded) if isinstance(decoded, dict) else [],
    }
    if isinstance(decoded, dict):
        columns = decoded.get("columns")
        rows = decoded.get("data")
        info["columns_type"] = type(columns).__name__
        info["row_data_type"] = type(rows).__name__
        if isinstance(columns, list):
            info["columns"] = columns
        if isinstance(rows, list):
            info["row_count"] = len(rows)
            info["sample_rows"] = rows[:3]
        if decoded.get("error") is not None:
            info["error"] = decoded.get("error")
        if decoded.get("showMessage") is not None:
            info["showMessage"] = decoded.get("showMessage")
    elif isinstance(decoded, list):
        info["row_count"] = len(decoded)
        info["sample_rows"] = decoded[:3]
    return info


def _csv_contract(payload: bytes) -> dict:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"csv_valid_utf8": False}
    if not text.strip():
        return {"csv_valid_utf8": True, "empty": True, "columns": [], "row_count": 0}
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)
    return {
        "csv_valid_utf8": True,
        "empty": False,
        "columns": rows[0] if rows else [],
        "row_count": max(0, len(rows) - 1),
        "sample_rows": rows[1:4],
    }


def probe(output_dir: Path) -> dict:
    # Bootstrap from the actual official report page. NSE may reject the
    # generic homepage from hosted CI even while report pages remain reachable.
    bootstrap_payload, bootstrap_meta = _get(
        PAGE, BASE + "/", "text/html,*/*"
    )
    if not bootstrap_payload or bootstrap_meta["http_status"] != 200:
        raise RuntimeError("NSE session bootstrap failed")

    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    output_dir.mkdir(parents=True, exist_ok=True)
    observations = []
    for option_type in OPTION_TYPES:
        query = {
            "optionType": option_type,
            "from": PAGE_DISPLAYED_FROM,
            "to": PAGE_DISPLAYED_TO,
        }
        json_url = ENDPOINT + "?" + urlencode(query)
        csv_url = ENDPOINT + "?" + urlencode({**query, "csv": "true"})
        json_payload, json_meta = _get(json_url, PAGE, "application/json,text/plain,*/*")
        csv_payload, csv_meta = _get(csv_url, PAGE, "text/csv,application/octet-stream,*/*")
        for kind, payload, metadata, contract in (
            ("json", json_payload, json_meta, _json_contract(json_payload)),
            ("csv", csv_payload, csv_meta, _csv_contract(csv_payload)),
        ):
            if not payload:
                raise ValueError(f"NSE {option_type} {kind} endpoint returned an empty body")
            digest = hashlib.sha256(payload).hexdigest()
            path = output_dir / f"{option_type}_{kind}_{digest[:16]}.{kind}"
            path.write_bytes(payload)
            observations.append({
                "option_type": option_type,
                "format": kind,
                "request_url": json_url if kind == "json" else csv_url,
                "retrieved_at_utc": retrieved,
                **metadata,
                "bytes": len(payload),
                "sha256": digest,
                "saved_path": str(path),
                "contract": contract,
            })
    report = {
        "source_page": PAGE,
        "endpoint": ENDPOINT,
        "retrieved_at_utc": retrieved,
        "date_range_source": "official page-displayed range at source inspection",
        "from": PAGE_DISPLAYED_FROM,
        "to": PAGE_DISPLAYED_TO,
        "session_bootstrap": {
            "http_status": bootstrap_meta["http_status"],
            "bytes": len(bootstrap_payload),
        },
        "observations": observations,
        "boundary": (
            "Named bulk/block transaction disclosures, not proof of beneficial ownership "
            "or FII status. Client names require independent classification."
        ),
    }
    manifest = output_dir / "probe_manifest.json"
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nse-deals-probe"))
    args = parser.parse_args()
    print(json.dumps(probe(args.output_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
