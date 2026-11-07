"""Probe official NSE historical cash and UDiFF archives before backfill."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from datetime import date, datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://nsearchives.nseindia.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
DEFAULT_DATES = ("2026-09-01", "2026-10-30", "2026-11-06")
SOURCES = {
    "cash_udiff": {
        "url": BASE + "/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip",
        "kind": "zip",
        "required": {"TradDt", "ISIN", "TckrSymb", "SctySrs", "ClsPric"},
        "date_field": "TradDt",
    },
    "fo_udiff": {
        "url": BASE + "/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip",
        "kind": "zip",
        "required": {"TradDt", "FinInstrmTp", "TckrSymb", "XpryDt"},
        "date_field": "TradDt",
    },
    "cash_delivery_full": {
        "url": BASE + "/products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
        "kind": "csv",
        "required": {"SYMBOL", "SERIES", "CLOSE_PRICE", "DELIV_QTY", "DELIV_PER"},
        "date_field": "DATE1",
    },
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse_trade_date(value: str) -> date:
    text = str(value or "").strip()
    for candidate in (text[:10], text):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                pass
    raise ValueError(f"unrecognized archive trade date: {value!r}")


def _decode_csv(payload: bytes, source: str) -> tuple[list[dict], list[str]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{source}: CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError(f"{source}: CSV has no header")
    fields = [str(item).strip() for item in reader.fieldnames]
    rows = []
    for row in reader:
        rows.append({
            str(key).strip(): (value.strip() if isinstance(value, str) else value)
            for key, value in row.items()
        })
    return rows, fields


def inspect_payload(payload: bytes, source: str, expected_date: str) -> dict:
    spec = SOURCES[source]
    if spec["kind"] == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if len(names) != 1:
                    raise ValueError(f"{source}: expected one CSV in ZIP, found {len(names)}")
                raw_csv = archive.read(names[0])
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{source}: response is not a valid ZIP") from exc
    else:
        raw_csv = payload

    rows, fields = _decode_csv(raw_csv, source)
    missing = spec["required"] - set(fields)
    if missing:
        raise ValueError(f"{source}: schema missing required fields {sorted(missing)}")
    if not rows:
        raise ValueError(f"{source}: historical CSV contains no rows")
    expected = date.fromisoformat(expected_date)
    observed_dates = {
        _parse_trade_date(row[spec["date_field"]])
        for row in rows
        if row.get(spec["date_field"])
    }
    if observed_dates != {expected}:
        raise ValueError(
            f"{source}: expected only {expected}, observed {sorted(observed_dates)}"
        )
    return {
        "source": source,
        "expected_trade_date": expected_date,
        "observed_trade_dates": sorted(item.isoformat() for item in observed_dates),
        "row_count": len(rows),
        "column_count": len(fields),
        "columns": fields,
        "zip_member": names[0] if spec["kind"] == "zip" else None,
        "csv_sha256": _sha256(raw_csv),
        "raw_payload_sha256": _sha256(payload),
        "raw_payload_bytes": len(payload),
    }


def _download(opener, url: str, source: str) -> bytes:
    request = Request(url, headers={**HEADERS, "Referer": "https://www.nseindia.com/all-reports"})
    for attempt in range(4):
        try:
            with opener.open(request, timeout=60) as response:
                if response.status != 200:
                    raise RuntimeError(f"{source}: unexpected HTTP {response.status}")
                payload = response.read()
                if not payload:
                    raise ValueError(f"{source}: empty historical response")
                return payload
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{source}: historical archive HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{source}: historical archive request failed: {exc}") from exc
    raise RuntimeError(f"{source}: historical archive failed after retries")


def run_probe(
    root: Path = Path("."),
    dates: tuple[str, ...] = DEFAULT_DATES,
) -> dict:
    parsed_dates = [date.fromisoformat(item) for item in dates]
    if not parsed_dates or len(parsed_dates) != len(set(parsed_dates)):
        raise ValueError("probe dates must be non-empty and unique")
    if tuple(sorted(parsed_dates)) != tuple(parsed_dates):
        raise ValueError("probe dates must be supplied oldest to newest")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    output = root / "data/raw/historical_archive_probe"
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for trade_date in parsed_dates:
        ymd = trade_date.strftime("%Y%m%d")
        params = {
            "ymd": ymd,
            "ddmmyyyy": trade_date.strftime("%d%m%Y"),
        }
        for source, spec in SOURCES.items():
            url = spec["url"].format(**params)
            payload = _download(opener, url, source)
            inspected = inspect_payload(payload, source, trade_date.isoformat())
            suffix = ".zip" if spec["kind"] == "zip" else ".csv"
            path = raw_dir / trade_date.isoformat() / f"{source}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and _sha256(path.read_bytes()) != inspected["raw_payload_sha256"]:
                raise RuntimeError(f"immutable historical probe payload changed: {path}")
            if not path.exists():
                path.write_bytes(payload)
            results.append({
                **inspected,
                "url": url,
                "raw_path": str(path),
            })

    manifest = {
        "dataset": "nse_historical_archive_availability_probe",
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "dates_tested": [item.isoformat() for item in parsed_dates],
        "sources_tested": list(SOURCES),
        "result_count": len(results),
        "results": results,
        "scope_note": (
            "Discovery/validation only. This probe does not backfill production "
            "stock history or treat unavailable dates as zero."
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "probe_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--dates", default=",".join(DEFAULT_DATES))
    args = parser.parse_args()
    dates = tuple(item.strip() for item in args.dates.split(",") if item.strip())
    print(json.dumps(run_probe(args.repo_root, dates), indent=2))


if __name__ == "__main__":
    main()
