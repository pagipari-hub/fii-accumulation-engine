"""Probe official NSE equity shareholding master, schema and RSS disclosures."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
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



def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xbrl_contract(payload: bytes) -> dict:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("NSE shareholding XBRL link did not return valid XML") from exc
    namespaces = []
    for _, namespace in ET.iterparse(io.BytesIO(payload), events=("start-ns",)):
        if namespace not in namespaces:
            namespaces.append(namespace)
    contexts = []
    for element in root.iter():
        if _local_name(element.tag).lower() != "context":
            continue
        context = {
            "id": element.attrib.get("id"),
            "identifier": None,
            "period": {},
            "dimensions": [],
        }
        for child in element.iter():
            local = _local_name(child.tag).lower()
            if local == "identifier":
                context["identifier"] = (child.text or "").strip()
            elif local in {"instant", "startdate", "enddate", "forever"}:
                context["period"][local] = (child.text or "").strip()
            elif local in {"explicitmember", "typedmember"}:
                context["dimensions"].append({
                    "dimension": child.attrib.get("dimension"),
                    "member": (child.text or "").strip(),
                })
        contexts.append(context)
    facts = []
    for element in root.iter():
        if not element.attrib.get("contextRef"):
            continue
        value = (element.text or "").strip()
        if value == "":
            continue
        facts.append({
            "name": _local_name(element.tag),
            "value": value,
            "context_ref": element.attrib.get("contextRef"),
            "unit_ref": element.attrib.get("unitRef"),
            "decimals": element.attrib.get("decimals"),
        })
    interesting = re.compile(
        r"foreign|portfolio|fpi|fii|promoter|public|shareholding|shareholder|total|shares|category",
        re.I,
    )
    schema_refs = [
        element.attrib.get("{http://www.w3.org/1999/xlink}href")
        for element in root.iter()
        if _local_name(element.tag).lower() == "schemaref"
    ]
    return {
        "root_tag": _local_name(root.tag),
        "namespace_count": len(namespaces),
        "namespaces": namespaces[:30],
        "schema_refs": [x for x in schema_refs if x],
        "context_count": len(contexts),
        "context_sample": contexts[:10],
        "unit_count": sum(1 for x in root.iter() if _local_name(x.tag).lower() == "unit"),
        "fact_count": len(facts),
        "facts_of_interest": [x for x in facts if interesting.search(x["name"])][:200],
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

    master_rows = json.loads(master_payload.decode("utf-8-sig"))
    rss_info = _rss_contract(rss_payload)
    sample_records = []
    if isinstance(master_rows, list):
        hdfc = next((row for row in master_rows if row.get("symbol") == "HDFCBANK"), None)
        revised = next((row for row in master_rows if row.get("revisedData") == "Revised"), None)
        sample_records.extend(row for row in (hdfc, revised) if row)
    for item in rss_info["sample_items"][:2]:
        if item.get("link"):
            sample_records.append({"xbrl": item["link"], "symbol": None, "source": "rss"})
    seen_xbrl = set()
    xbrl_samples = []
    for index, record in enumerate(sample_records):
        url = record.get("xbrl")
        if not url or url in seen_xbrl or len(xbrl_samples) >= 4:
            continue
        seen_xbrl.add(url)
        if not url.startswith("https://nsearchives.nseindia.com/corporate/xbrl/"):
            xbrl_samples.append({"url": url, "status": "untrusted_xbrl_url"})
            continue
        try:
            xbrl_payload, xbrl_meta = _get(url, PAGE, "application/xml,text/xml,*/*")
            xbrl_saved = _save(
                output_dir, f"shareholding_xbrl_{index}", xbrl_payload,
                xbrl_meta, _xbrl_contract(xbrl_payload),
            )
            xbrl_samples.append({
                **xbrl_saved,
                "symbol": record.get("symbol"),
                "record_id": record.get("recordId"),
                "as_of_date": record.get("date"),
                "submission_date": record.get("submissionDate"),
                "revision_status": record.get("revisedData"),
            })
        except Exception as exc:
            xbrl_samples.append({"url": url, "status": "fetch_or_parse_error", "error": str(exc)})

    report = {
        "publisher": "National Stock Exchange of India",
        "page_url": PAGE,
        "retrieved_at_utc": retrieved_at,
        "session_bootstrap": {
            "http_status": bootstrap_meta["http_status"],
            "bytes": len(bootstrap),
        },
        "observations": observations,
        "xbrl_samples": xbrl_samples,
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
