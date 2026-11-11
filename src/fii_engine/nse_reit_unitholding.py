"""Normalize official NSE REIT unitholding PDFs as a separate ownership data type.

This parser deliberately does not merge REIT units into the equity shareholding
XBRL dataset. Every output row is tied to an exact NSE announcement symbol,
quarter-end disclosure, attachment path, and SHA-256 digest.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pypdf import PdfReader

ANNOUNCEMENTS = Path("artifacts/nse-shareholding-announcements/nse_shareholding_unitholding_announcements_manifest.json")
UNIVERSE = Path("data/raw/universe/nifty500_current.csv")
OUTPUT = Path("data/raw/shareholding/reit_unitholding_current.csv")
MANIFEST = Path("data/raw/shareholding/reit_unitholding_current_manifest.json")
REPORTING_DATE = "2026-09-30"
FPI_ROW = re.compile(
    r"Foreign\\s+Portfolio\\s+Investors\\s+([0-9,]+|-)\\s+([0-9]+(?:\\.[0-9]+)?|-)",
    re.IGNORECASE,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pdf_text(payload: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted REIT unitholding PDF is not supported")
        text = "\\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ValueError(f"could not extract text from official REIT PDF: {exc}") from exc
    if not text.strip():
        raise ValueError("official REIT PDF contains no extractable text")
    return text


def _quarter_is_current(text: str) -> bool:
    normalized = re.sub(r"\\s+", " ", text).lower()
    return (
        "quarter ended september 30, 2026" in normalized
        or "quarter ended 30 september 2026" in normalized
        or "quarter ended september 30 2026" in normalized
    )


def parse_fpi_unitholding_rows(text: str) -> dict:
    """Sum explicitly reported sponsor/public FPI rows; dash means publisher-reported nil."""
    rows = []
    for line in text.splitlines():
        match = FPI_ROW.search(re.sub(r"\\s+", " ", line).strip())
        if not match:
            continue
        raw_units, raw_pct = match.groups()
        if raw_units == "-" or raw_pct == "-":
            if raw_units != "-" or raw_pct != "-":
                raise ValueError("REIT FPI row has a partial dash; refusing to infer a value")
            units, pct = 0, Decimal("0")
        else:
            try:
                units = int(raw_units.replace(",", ""))
                pct = Decimal(raw_pct)
            except (ValueError, InvalidOperation) as exc:
                raise ValueError("REIT FPI row contains an invalid numeric value") from exc
        if units < 0 or not pct.is_finite() or pct < 0 or pct > 100:
            raise ValueError("REIT FPI units/percentage outside valid range")
        rows.append({"units": units, "percentage_points": pct})
    if not rows:
        raise ValueError("official REIT PDF has no parseable Foreign Portfolio Investors rows")
    total_units = sum(row["units"] for row in rows)
    total_pct = sum((row["percentage_points"] for row in rows), Decimal("0"))
    if total_pct > 100:
        raise ValueError("summed REIT FPI percentage exceeds 100")
    return {
        "fpi_units": total_units,
        "fpi_ownership_percentage_points": str(total_pct.normalize()),
        "reported_fpi_row_count": len(rows),
    }


def normalize(root: Path = Path("."), announcements_path: Path = ANNOUNCEMENTS) -> dict:
    root = root.resolve()
    announcements_path = announcements_path if announcements_path.is_absolute() else root / announcements_path
    announcement_manifest = json.loads(announcements_path.read_text(encoding="utf-8"))
    universe_path = root / UNIVERSE
    with universe_path.open(newline="", encoding="utf-8-sig") as handle:
        universe = {row["symbol"].strip().upper(): row for row in csv.DictReader(handle)}
    output_rows = []
    source_evidence = []
    for observation in announcement_manifest.get("observations", []):
        symbol = str(observation.get("symbol") or "").strip().upper()
        if observation.get("segment") != "invitsreits":
            continue
        stock = universe.get(symbol)
        if not stock:
            raise ValueError(f"REIT announcement symbol is not in the dated universe: {symbol}")
        matching = [
            item for item in observation.get("matching_announcements", [])
            if REPORTING_DATE in str(item.get("attchmntText") or "")
            or "September 30, 2026" in str(item.get("attchmntText") or "")
        ]
        selected = []
        for announcement in matching:
            seq_id = str(announcement.get("seq_id") or "")
            attachment = next(
                (item for item in observation.get("attachments", []) if str(item.get("seq_id")) == seq_id),
                None,
            )
            if attachment and attachment.get("status") == "captured":
                selected.append((announcement, attachment))
        if not selected:
            continue
        if len(selected) != 1:
            raise ValueError(f"multiple current-quarter REIT unitholding attachments for {symbol}")
        announcement, attachment = selected[0]
        relative = Path(str(attachment.get("raw_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe REIT attachment path for {symbol}")
        payload = (root / relative).read_bytes()
        digest = _sha256(payload)
        if digest != attachment.get("sha256"):
            raise ValueError(f"REIT attachment SHA-256 mismatch for {symbol}")
        text = _pdf_text(payload)
        if not _quarter_is_current(text):
            raise ValueError(f"REIT PDF quarter-end does not match {REPORTING_DATE}: {symbol}")
        # Announcement symbol is exact; require the same symbol to be visible in the PDF cover.
        if not re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", text.upper()):
            raise ValueError(f"REIT PDF does not identify the queried security: {symbol}")
        parsed = parse_fpi_unitholding_rows(text)
        output_rows.append({
            "reporting_date": REPORTING_DATE,
            "symbol": symbol,
            "company_name": stock["company_name"],
            "isin": stock["isin"],
            "ownership_data_type": "reit_unitholding_pattern",
            **parsed,
            "announcement_seq_id": str(announcement["seq_id"]),
            "announcement_date": str(announcement.get("an_dt") or ""),
            "attachment_path": str(relative),
            "attachment_sha256": digest,
        })
        source_evidence.append({
            "symbol": symbol,
            "seq_id": str(announcement["seq_id"]),
            "attachment_path": str(relative),
            "attachment_sha256": digest,
        })
    output_rows.sort(key=lambda row: row["symbol"])
    fields = [
        "reporting_date", "symbol", "company_name", "isin", "ownership_data_type",
        "fpi_units", "fpi_ownership_percentage_points", "reported_fpi_row_count",
        "announcement_seq_id", "announcement_date", "attachment_path", "attachment_sha256",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\\n")
    writer.writeheader()
    writer.writerows(output_rows)
    payload = stream.getvalue().encode("utf-8")
    output_path = root / OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    manifest = {
        "dataset": "official_nse_reit_unitholding_fpi_ownership",
        "publisher": "National Stock Exchange of India",
        "reporting_date": REPORTING_DATE,
        "status": "complete" if len(output_rows) == 3 else "partial",
        "expected_reit_count": 3,
        "parsed_reit_count": len(output_rows),
        "rows": output_rows,
        "source_evidence": source_evidence,
        "output_path": str(OUTPUT),
        "output_sha256": _sha256(payload),
        "scope_boundary": "REIT unit ownership is kept separate from equity shareholding XBRL; no conversion of units to equity shares is performed.",
    }
    manifest_path = root / MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--announcements", type=Path, default=ANNOUNCEMENTS)
    args = parser.parse_args()
    print(json.dumps(normalize(args.root, args.announcements), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
