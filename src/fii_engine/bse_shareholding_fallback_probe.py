"""Probe BSE quarterly shareholding filing availability for NSE-master gaps.

This collector records official BSE filing-index JSON and XBRL attachment bytes.
It does not parse ownership or infer stock-level FPI activity.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

API_BASE = "https://api.bseindia.com/BseIndiaAPI/api/Corp_Shareholding_ng/w"
BSE_BASE = "https://www.bseindia.com"
REFERER = "https://www.bseindia.com/corporates/Sharehold_Searchnew.aspx"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _get(url: str, accept: str) -> tuple[bytes, dict]:
    request = Request(url, headers={
        "User-Agent": UA,
        "Referer": REFERER,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    for attempt in range(4):
        try:
            with urlopen(request, timeout=60) as response:
                return response.read(), {
                    "http_status": response.status,
                    "content_type": response.headers.get("Content-Type", ""),
                    "response_url": response.geturl(),
                }
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"BSE request failed ({exc.code}): {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"BSE request failed: {url}: {exc}") from exc
    raise RuntimeError(f"BSE retries exhausted: {url}")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _quarter_key(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("2026-09-30"):
        return "2026-09-30"
    if text.lower() in {"september 2026", "sep 2026"}:
        return "2026-09-30"
    return ""


def select_quarter_rows(payload: bytes, candidate: dict) -> list[dict]:
    try:
        decoded = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BSE shareholding index did not return valid JSON") from exc
    table = decoded.get("Table") if isinstance(decoded, dict) else None
    if not isinstance(table, list):
        raise ValueError("BSE shareholding index JSON has no Table array")
    selected = []
    expected_code = str(candidate["bse_scrip_code"]).strip()
    for row in table:
        if not isinstance(row, dict):
            continue
        code = str(row.get("FLD_ScripCode") or "").strip()
        if code and code != expected_code:
            continue
        quarter = _quarter_key(row.get("EndDate") or row.get("DisplayDT") or row.get("sQtrName"))
        if quarter == candidate["reporting_date"]:
            selected.append(row)
    return sorted(
        selected,
        key=lambda row: str(row.get("D") or row.get("broadcastTime") or ""),
        reverse=True,
    )


def _safe_attachment_url(path: str) -> str:
    value = str(path or "").strip()
    parsed = urlparse(urljoin(BSE_BASE, value))
    if parsed.scheme != "https" or parsed.netloc != "www.bseindia.com" or not parsed.path.startswith("/XBRLFILES/"):
        raise ValueError(f"untrusted BSE shareholding attachment path: {value}")
    return parsed.geturl()


def probe(candidates_csv: Path, output_dir: Path, bse_udiff_path: Path) -> dict:
    with candidates_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    if not candidates:
        raise ValueError("BSE shareholding fallback candidate file is empty")
    bse_payload = bse_udiff_path.read_bytes()
    bse_digest = _sha256(bse_payload)
    with bse_udiff_path.open("r", encoding="utf-8-sig", newline="") as handle:
        bse_rows = list(csv.DictReader(handle))
    bse_by_symbol = {
        str(row.get("TckrSymb") or "").strip().upper(): row for row in bse_rows
    }
    for candidate in candidates:
        if candidate.get("bse_udiff_source_sha256") != bse_digest:
            raise ValueError("BSE UDiFF source SHA-256 differs from candidate audit")
        symbol = candidate["bse_symbol"].strip().upper()
        row = bse_by_symbol.get(symbol)
        if not row:
            raise ValueError(f"BSE UDiFF source has no exact symbol row: {symbol}")
        if (
            str(row.get("FinInstrmId") or "").strip() != candidate["bse_scrip_code"].strip()
            or str(row.get("ISIN") or "").strip().upper() != candidate["universe_isin"].strip().upper()
            or str(row.get("SctySrs") or "").strip().upper() != candidate["bse_series"].strip().upper()
        ):
            raise ValueError(f"BSE UDiFF identifier/ISIN/series mismatch: {symbol}")
    output_dir.mkdir(parents=True, exist_ok=True)
    observations = []
    for index, candidate in enumerate(candidates):
        code = str(candidate["bse_scrip_code"]).strip()
        url = f"{API_BASE}?scripcode={quote(code)}&flag=0&indtype="
        record = {
            "symbol": candidate["symbol"],
            "reporting_date": candidate["reporting_date"],
            "universe_isin": candidate["universe_isin"],
            "bse_scrip_code": code,
            "bse_symbol": candidate["bse_symbol"],
            "bse_series": candidate["bse_series"],
            "index_url": url,
        }
        try:
            payload, response = _get(url, "application/json,*/*")
            record.update(response)
            record["index_bytes"] = len(payload)
            record["index_sha256"] = _sha256(payload)
            raw_json = output_dir / f"bse_shareholding_index_{candidate['symbol']}_{record['index_sha256'][:16]}.json"
            raw_json.write_bytes(payload)
            record["raw_index_path"] = str(raw_json)
            selected = select_quarter_rows(payload, candidate)
            record["quarter_row_count"] = len(selected)
            if not selected:
                record["status"] = "no_quarter_filing"
            else:
                chosen = selected[0]
                record["quarter_record"] = chosen
                record["is_xbrl"] = str(chosen.get("IsXBRL", "")).strip() in {"1", "True", "true"}
                if not record["is_xbrl"]:
                    record["status"] = "quarter_filing_without_verified_xbrl_attachment"
                else:
                    attachment_url = _safe_attachment_url(chosen.get("XBRLAttachment", ""))
                    html, attachment_meta = _get(attachment_url, "text/html,application/xhtml+xml,*/*")
                    if len(html) < 1000 or b"in-bse-shp" not in html.lower():
                        raise ValueError("BSE attachment lacks expected iXBRL taxonomy marker")
                    digest = _sha256(html)
                    raw_html = output_dir / f"bse_shareholding_xbrl_{candidate['symbol']}_{digest[:16]}.html"
                    raw_html.write_bytes(html)
                    record.update({
                        "status": "quarter_xbrl_attachment_captured",
                        "attachment_url": attachment_url,
                        "attachment_http_status": attachment_meta["http_status"],
                        "attachment_content_type": attachment_meta["content_type"],
                        "attachment_bytes": len(html),
                        "attachment_sha256": digest,
                        "raw_attachment_path": str(raw_html),
                    })
        except Exception as exc:
            record["status"] = "probe_error"
            record["error"] = str(exc)
        observations.append(record)
        if index + 1 < len(candidates):
            time.sleep(0.25)

    report = {
        "publisher": "Bombay Stock Exchange",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "reporting_date": "2026-09-30",
        "candidate_count": len(candidates),
        "bse_udiff_source_sha256": bse_digest,
        "observations": observations,
        "status_counts": {},
        "scope_boundary": (
            "BSE quarterly shareholding filing availability only; captured attachments are not yet parsed "
            "into ownership rows and do not establish daily FPI transactions."
        ),
    }
    for row in observations:
        status = row["status"]
        report["status_counts"][status] = report["status_counts"].get(status, 0) + 1
    report_path = output_dir / "bse_shareholding_fallback_probe_manifest.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bse-udiff", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(
        probe(args.candidates, args.output_dir, args.bse_udiff),
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
