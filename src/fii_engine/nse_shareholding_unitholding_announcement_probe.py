"""Probe official NSE shareholding/unitholding announcements for the six remaining securities."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from fii_engine.nse_shareholding_probe import BASE, _get

API = "https://www.nseindia.com/api/corporate-announcements"
ANNOUNCEMENTS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
DEFAULT_CANDIDATES = [
    {"symbol": "ABBOTINDIA", "segment": "equities"},
    {"symbol": "ALKEM", "segment": "equities"},
    {"symbol": "MCX", "segment": "equities"},
    {"symbol": "EMBASSY", "segment": "invitsreits"},
    {"symbol": "BIRET", "segment": "invitsreits"},
    {"symbol": "BAGMANE", "segment": "invitsreits"},
]
TERMS = ("shareholding pattern", "unitholding pattern")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_pattern_announcement(row: dict) -> bool:
    text = " ".join(str(row.get(key, "")) for key in ("desc", "attchmntText", "attchmntFile"))
    return any(term in text.lower() for term in TERMS)


def _attachment_urls(row: dict) -> list[str]:
    urls = []
    for key, value in row.items():
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            if key.lower() in {"attchmntfile", "xbrlfile", "xbrl", "xbrllink", "attachmenturl"}:
                urls.append(value)
    return list(dict.fromkeys(urls))


def probe(
    output_dir: Path,
    candidates: list[dict] | None = None,
    from_date: str = "01-01-2026",
    to_date: str = "11-11-2026",
    delay: float = 0.25,
) -> dict:
    candidates = candidates or DEFAULT_CANDIDATES
    output_dir.mkdir(parents=True, exist_ok=True)
    _, bootstrap = _get(ANNOUNCEMENTS_PAGE, BASE, "text/html,*/*")
    observations = []
    for index, candidate in enumerate(candidates):
        symbol = candidate["symbol"].strip().upper()
        segment = candidate["segment"].strip().lower()
        query = (
            f"{API}?index={quote(segment)}&from_date={quote(from_date)}"
            f"&to_date={quote(to_date)}&symbol={quote(symbol)}"
        )
        record = {"symbol": symbol, "segment": segment, "query_url": query}
        try:
            payload, response = _get(query, ANNOUNCEMENTS_PAGE, "application/json,*/*")
            digest = _sha256(payload)
            raw_path = output_dir / f"{segment}_{symbol}_announcements_{digest[:16]}.json"
            raw_path.write_bytes(payload)
            decoded = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(decoded, list):
                raise ValueError("NSE corporate-announcements response was not a JSON array")
            matched = [row for row in decoded if isinstance(row, dict) and _is_pattern_announcement(row)]
            attachment_records = []
            for ann_index, announcement in enumerate(matched):
                for attachment_url in _attachment_urls(announcement):
                    attachment = {
                        "seq_id": announcement.get("seq_id"),
                        "description": announcement.get("desc"),
                        "attachment_url": attachment_url,
                    }
                    try:
                        attachment_payload, attachment_meta = _get(
                            attachment_url, ANNOUNCEMENTS_PAGE, "*/*"
                        )
                        suffix = Path(urlparse(attachment_url).path).suffix or ".bin"
                        file_name = (
                            f"{symbol}_{announcement.get('seq_id', ann_index)}_"
                            f"{_sha256(attachment_payload)[:16]}{suffix}"
                        )
                        attachment_path = output_dir / "attachments" / file_name
                        attachment_path.parent.mkdir(parents=True, exist_ok=True)
                        attachment_path.write_bytes(attachment_payload)
                        attachment.update({
                            **attachment_meta,
                            "status": "captured",
                            "raw_path": str(attachment_path),
                            "bytes": len(attachment_payload),
                            "sha256": _sha256(attachment_payload),
                        })
                    except Exception as exc:
                        attachment.update({"status": "download_error", "error": str(exc)})
                    attachment_records.append(attachment)
                    time.sleep(delay)
            record.update({
                **response,
                "status": "captured",
                "response_sha256": digest,
                "raw_path": str(raw_path),
                "announcement_count": len(decoded),
                "matching_pattern_announcement_count": len(matched),
                "matching_announcements": matched,
                "attachments": attachment_records,
            })
        except Exception as exc:
            record.update({"status": "probe_error", "error": str(exc)})
        observations.append(record)
        if index + 1 < len(candidates):
            time.sleep(delay)
    report = {
        "publisher": "National Stock Exchange of India",
        "endpoint": API,
        "from_date": from_date,
        "to_date": to_date,
        "session_bootstrap": bootstrap,
        "candidates": candidates,
        "observations": observations,
        "matching_announcement_counts": {
            row["symbol"]: row.get("matching_pattern_announcement_count", 0)
            for row in observations
        },
        "scope_boundary": (
            "Official NSE announcement and attachment discovery only. Captured attachments are not ingested "
            "into ownership history until reporting period, category totals, security identity and source hashes "
            "are validated. Shareholding and unitholding patterns remain distinct data types."
        ),
    }
    path = output_dir / "nse_shareholding_unitholding_announcements_manifest.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--from-date", default="01-01-2026")
    parser.add_argument("--to-date", default="11-11-2026")
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    report = probe(args.output_dir, from_date=args.from_date, to_date=args.to_date, delay=args.delay)
    print(json.dumps({
        "matching_announcement_counts": report["matching_announcement_counts"],
        "attachment_counts": {
            row["symbol"]: len(row.get("attachments", []))
            for row in report["observations"]
        },
    }, indent=2))


if __name__ == "__main__":
    main()
