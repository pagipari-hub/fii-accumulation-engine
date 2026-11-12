"""Probe official issuer-hosted shareholding disclosures when exchange APIs block access.

These records remain a separately typed fallback and never overwrite NSE XBRL rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

REPORTING_DATE = "2026-09-30"
UNIVERSE = Path("data/raw/universe/nifty500_current.csv")
CROSSWALK = Path("data/raw/shareholding/identity_evidence/shareholding_bse_scripcode_isin_crosswalk.csv")
OUTPUT = Path("data/raw/shareholding/issuer_shareholding_fallback_current.csv")
MANIFEST = Path("data/raw/shareholding/issuer_shareholding_fallback_current_manifest.json")
SOURCES = {
    "ABBOTINDIA": {
        "url": "https://www.abbott.com/content/dam/abbottdam/corp/gmea/global/corporate-sites/in/investors/shareholding/Shareholding-Pattern-September-30-2026.pdf",
        "format": "pdf",
        "bse_scrip_code": "500488",
    },
    "MCX": {
        "url": "https://www.mcxindia.com/investor-relations/shareholder-information",
        "format": "html",
        "bse_scrip_code": "534091",
    },
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fetch(url: str) -> tuple[bytes, str, int]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FPIResearchEvidence/1.0)",
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
        return payload, response.headers.get("Content-Type", ""), int(response.status)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def _verified_identity(root: Path, symbol: str, expected_scrip: str) -> dict:
    """Join issuer data to the dated universe using the official BSE UDiFF row.

    The XBRL-derived crosswalk only contains issuers that filed an NSE XBRL
    disclosure. It therefore cannot be used to validate the very missing
    issuers this fallback is intended to recover.
    """
    universe_path = root / UNIVERSE
    with universe_path.open(newline="", encoding="utf-8-sig") as handle:
        universe = {row["symbol"].strip().upper(): row for row in csv.DictReader(handle)}
    stock = universe.get(symbol)
    if not stock:
        raise ValueError(f"symbol is absent from dated Nifty 500 universe: {symbol}")

    udiff_paths = sorted(
        (root / "data/raw/shareholding/identity_evidence").glob(
            f"BhavCopy_BSE_CM_0_0_0_{REPORTING_DATE.replace('-', '')}_F_0000_*.csv"
        )
    )
    if len(udiff_paths) != 1:
        raise ValueError(f"expected one dated BSE UDiFF file, found {len(udiff_paths)}")
    udiff_path = udiff_paths[0]
    with udiff_path.open(newline="", encoding="utf-8-sig") as handle:
        matches = [
            row for row in csv.DictReader(handle)
            if row.get("Sgmt") == "CM"
            and row.get("Src") == "BSE"
            and row.get("FinInstrmTp") == "STK"
            and row.get("TckrSymb", "").strip().upper() == symbol
        ]
    if len(matches) != 1:
        raise ValueError(f"expected one dated BSE UDiFF identity row for {symbol}, found {len(matches)}")
    row = matches[0]
    universe_isin = stock["isin"].strip().upper()
    if row.get("TradDt") != REPORTING_DATE:
        raise ValueError(f"BSE UDiFF trade date mismatch for {symbol}")
    if row.get("FinInstrmId", "").strip() != expected_scrip:
        raise ValueError(f"dated BSE scrip code mismatch for {symbol}")
    if row.get("ISIN", "").strip().upper() != universe_isin:
        raise ValueError(f"dated BSE UDiFF ISIN does not match Nifty 500 universe for {symbol}")
    if row.get("TckrSymb", "").strip().upper() != symbol:
        raise ValueError(f"dated BSE UDiFF symbol mismatch for {symbol}")
    return {
        "company_name": stock["company_name"],
        "isin": stock["isin"],
        "identity_join_method": "verified_exact_dated_bse_udiff_symbol_scripcode_isin_match",
        "identity_evidence_path": str(udiff_path.relative_to(root)),
        "identity_evidence_sha256": _sha256(udiff_path.read_bytes()),
    }


def parse_abbott_pdf_text(text: str) -> dict:
    normalized = re.sub(r"\s+", " ", text)
    if "Abbott India Limited" not in normalized:
        raise ValueError("issuer PDF does not identify Abbott India Limited")
    if not re.search(r"ISIN\s+INE358A01014", normalized, re.IGNORECASE):
        raise ValueError("issuer PDF ISIN does not match ABBOTINDIA")
    if not re.search(r"Scrip\s+code\s+500488", normalized, re.IGNORECASE):
        raise ValueError("issuer PDF BSE scrip code does not match ABBOTINDIA")
    if not re.search(r"30-09-2026", normalized):
        raise ValueError("issuer PDF reporting date is not 2026-09-30")
    values = {}
    for category in ("I", "II"):
        pattern = re.compile(
            rf"Foreign Portfolio Investor(?:s)? Category {category}\s+\d+\s+([0-9,]+)\s+[0-9,]+\s+([0-9]+(?:\.[0-9]+)?)",
            re.IGNORECASE,
        )
        match = pattern.search(normalized)
        if not match:
            raise ValueError(f"issuer PDF missing FPI Category {category}")
        values[category] = (int(match.group(1).replace(",", "")), match.group(2))
    return {
        "fpi_category_i_shares": values["I"][0],
        "fpi_category_ii_shares": values["II"][0],
        "fpi_total_shares": values["I"][0] + values["II"][0],
        "fpi_category_i_ownership_percentage_points": values["I"][1],
        "fpi_category_ii_ownership_percentage_points": values["II"][1],
        "fpi_total_ownership_percentage_points": str(
            float(values["I"][1]) + float(values["II"][1])
        ),
        "category_breakdown_status": "reported_category_i_and_ii",
    }


def parse_mcx_html(payload: bytes) -> dict:
    parser = _TextExtractor()
    try:
        parser.feed(payload.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ValueError("MCX issuer page is not UTF-8") from exc
    text = re.sub(r"\s+", " ", " ".join(parser.parts))
    if "MCX (BSE: MCX)" not in text:
        raise ValueError("issuer page does not identify MCX")
    if "Shareholding Pattern as on September 30, 2026" not in text:
        raise ValueError("issuer page does not contain the 2026-09-30 shareholding table")
    match = re.search(
        r"Foreign Portfolio Investors\s+([0-9,]+)\s+([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("MCX issuer page has no parseable FPI total row")
    return {
        "fpi_category_i_shares": None,
        "fpi_category_ii_shares": None,
        "fpi_total_shares": int(match.group(1).replace(",", "")),
        "fpi_category_i_ownership_percentage_points": None,
        "fpi_category_ii_ownership_percentage_points": None,
        "fpi_total_ownership_percentage_points": match.group(2),
        "category_breakdown_status": "publisher_total_only_category_split_unavailable",
    }


def probe(root: Path = Path(".")) -> dict:
    root = root.resolve()
    raw_dir = root / "data/raw/shareholding/issuer_fallback"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows, observations = [], []
    for symbol, source in SOURCES.items():
        observation = {
            "symbol": symbol,
            "source_url": source["url"],
            "source_type": source["format"],
            "status": "not_attempted",
        }
        try:
            identity = _verified_identity(root, symbol, source["bse_scrip_code"])
            payload, content_type, http_status = _fetch(source["url"])
            if http_status != 200 or not payload:
                raise RuntimeError(f"issuer source returned HTTP {http_status}")
            digest = _sha256(payload)
            suffix = "pdf" if source["format"] == "pdf" else "html"
            raw_path = Path("data/raw/shareholding/issuer_fallback") / f"{symbol}_{digest[:16]}.{suffix}"
            (root / raw_path).write_bytes(payload)
            if source["format"] == "pdf":
                reader = PdfReader(io.BytesIO(payload), strict=True)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                parsed = parse_abbott_pdf_text(text)
            else:
                parsed = parse_mcx_html(payload)
            row = {
                "reporting_date": REPORTING_DATE,
                "symbol": symbol,
                **identity,
                "ownership_data_type": "issuer_published_equity_shareholding",
                **parsed,
                "source_url": source["url"],
                "source_content_type": content_type,
                "source_http_status": http_status,
                "source_raw_path": str(raw_path),
                "source_sha256": digest,
            }
            rows.append(row)
            observation.update({
                "status": "parsed_verified_identity",
                "http_status": http_status,
                "content_type": content_type,
                "raw_path": str(raw_path),
                "sha256": digest,
                "fpi_total_shares": parsed["fpi_total_shares"],
                "fpi_total_ownership_percentage_points": parsed["fpi_total_ownership_percentage_points"],
                "category_breakdown_status": parsed["category_breakdown_status"],
            })
        except Exception as exc:
            observation.update({"status": "source_or_parse_error", "error": str(exc)})
        observations.append(observation)

    fields = [
        "reporting_date", "symbol", "company_name", "isin", "identity_join_method",
        "identity_evidence_path", "identity_evidence_sha256", "ownership_data_type", "fpi_category_i_shares", "fpi_category_ii_shares",
        "fpi_total_shares", "fpi_category_i_ownership_percentage_points",
        "fpi_category_ii_ownership_percentage_points", "fpi_total_ownership_percentage_points",
        "category_breakdown_status", "source_url", "source_content_type",
        "source_http_status", "source_raw_path", "source_sha256",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda row: row["symbol"]))
    csv_payload = stream.getvalue().encode("utf-8")
    csv_path = root / OUTPUT
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_bytes(csv_payload)
    manifest = {
        "dataset": "issuer_published_equity_shareholding_fpi_fallback",
        "reporting_date": REPORTING_DATE,
        "status": "complete" if len(rows) == len(SOURCES) else "partial",
        "expected_candidate_count": len(SOURCES),
        "parsed_verified_count": len(rows),
        "rows": sorted(rows, key=lambda row: row["symbol"]),
        "observations": observations,
        "output_path": str(OUTPUT),
        "output_sha256": _sha256(csv_payload),
        "scope_boundary": "Issuer-published equity shareholding is a separate fallback source. Missing category splits remain null; no issuer-source values overwrite NSE XBRL records.",
    }
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(probe(args.root), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
