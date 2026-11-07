"""Capture and validate NSE's official final UDiFF equity-derivatives bhavcopy."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
import time
from collections import Counter
from datetime import date, datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_URL = "https://www.nseindia.com"
REPORT_PAGE = f"{BASE_URL}/all-reports-derivatives"
CATALOGUE_URL = f"{BASE_URL}/api/daily-reports?key=FO"
ARCHIVE_HOST = "nsearchives.nseindia.com"
REPORT_KEY = "FO-UDIFF-BHAVCOPY-CSV"
FILENAME_RE = re.compile(
    r"^BhavCopy_NSE_FO_0_0_0_(?P<date>\d{8})_F_0000\.csv\.zip$"
)
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

# Core UDiFF tags required for this pipeline. Extra columns are retained and
# recorded; missing core tags fail closed so a schema change cannot pass silently.
REQUIRED_COLUMNS = {
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId",
    "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "FinInstrmNm",
    "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric",
    "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst",
    "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd", "NewBrdLotQty",
}
NUMERIC_COLUMNS = {
    "FinInstrmId", "StrkPric", "OpnPric", "HghPric", "LwPric", "ClsPric",
    "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst",
    "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd", "NewBrdLotQty",
}
ALLOWED_INSTRUMENT_TYPES = {"STF", "STO", "IDF", "IDO"}
KNOWN_COLUMNS = {
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId",
    "ISIN", "TckrSymb", "SctySrs", "XpryDt", "FininstrmActlXpryDt",
    "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric",
    "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric",
    "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks",
    "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
}


def _get(url: str, referer: str, accept: str, timeout: int = 60) -> bytes:
    request = Request(
        url,
        headers={**HEADERS, "Accept": accept, "Referer": referer},
    )
    for attempt in range(4):
        try:
            with HTTP.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP {response.status} for {url}")
                return response.read()
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE request failed for {url}: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"NSE request failed for {url}: {exc}") from exc
    raise RuntimeError(f"NSE request failed after retries for {url}")


def fetch_catalogue() -> dict:
    # Use one cookie session for homepage, report page, API and archive download.
    _get(BASE_URL + "/", BASE_URL + "/", "text/html,*/*")
    _get(REPORT_PAGE, BASE_URL + "/", "text/html,*/*")
    payload = _get(CATALOGUE_URL, REPORT_PAGE, "application/json,*/*")
    try:
        catalogue = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NSE FO daily-reports API did not return valid JSON") from exc
    if not isinstance(catalogue, dict):
        raise ValueError("NSE FO catalogue schema drift: expected JSON object")
    if not isinstance(catalogue.get("currentDate"), str):
        raise ValueError("NSE FO catalogue omitted currentDate")
    for key in ("CurrentDay", "PreviousDay"):
        if not isinstance(catalogue.get(key), list):
            raise ValueError(f"NSE FO catalogue omitted {key} report list")
    return catalogue


def _parse_catalogue_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d-%b-%Y").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid NSE catalogue tradingDate: {value!r}") from exc


def select_final_bhavcopy(catalogue: dict) -> dict:
    """Select the newest final UDiFF report from current/previous trade-day groups."""
    candidates = []
    for group_name in ("CurrentDay", "PreviousDay"):
        for item in catalogue[group_name]:
            if isinstance(item, dict) and item.get("fileKey") == REPORT_KEY:
                record = dict(item)
                record["_catalogue_group"] = group_name
                candidates.append(record)
    if not candidates:
        raise ValueError(f"NSE catalogue contains no {REPORT_KEY} report")

    candidates.sort(key=lambda item: _parse_catalogue_date(item.get("tradingDate")))
    selected = candidates[-1]
    trade_date = _parse_catalogue_date(selected.get("tradingDate"))
    filename = selected.get("fileActlName")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("NSE UDiFF catalogue returned an unsafe filename")
    match = FILENAME_RE.fullmatch(filename)
    if not match:
        raise ValueError(f"unexpected final UDiFF filename: {filename!r}")
    filename_date = datetime.strptime(match.group("date"), "%Y%m%d").date()
    if filename_date != trade_date:
        raise ValueError(
            f"catalogue/file date mismatch: {trade_date} vs {filename_date}"
        )
    file_path = selected.get("filePath")
    parsed = urlparse(file_path or "")
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARCHIVE_HOST
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"untrusted NSE archive path: {file_path!r}")
    return {
        "catalogue_group": selected["_catalogue_group"],
        "report_key": REPORT_KEY,
        "display_name": selected.get("displayName"),
        "trading_date": trade_date.isoformat(),
        "filename": filename,
        "download_url": file_path.rstrip("/") + "/" + filename,
        "reported_file_size": selected.get("fileSize"),
        "file_position": selected.get("filePosition"),
        "file_hash_key": selected.get("fileHashKey"),
        "catalogue_current_date": catalogue["currentDate"],
        "catalogue_previous_date": catalogue.get("previousDate"),
    }


def _read_zip(payload: bytes) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"UDiFF ZIP CRC check failed for {bad_member}")
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                raise ValueError(
                    f"expected one CSV in UDiFF ZIP; found {len(members)} files"
                )
            member = members[0]
            if not member.filename.lower().endswith(".csv"):
                raise ValueError(f"UDiFF ZIP member is not CSV: {member.filename}")
            if member.file_size <= 0 or member.file_size > 250_000_000:
                raise ValueError(
                    f"UDiFF expanded CSV size outside safety bounds: {member.file_size}"
                )
            return member.filename, archive.read(member)
    except zipfile.BadZipFile as exc:
        raise ValueError("NSE UDiFF response is not a valid ZIP archive") from exc


def _parse_number(value: str, column: str, row_number: int) -> None:
    if value == "":
        return
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(
            f"non-numeric {column} at CSV row {row_number}: {value!r}"
        ) from exc
    if not (number == number and abs(number) != float("inf")):
        raise ValueError(f"non-finite {column} at CSV row {row_number}")


def validate_udiff_csv(payload: bytes, expected_trade_date: str) -> dict:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("UDiFF CSV is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError("UDiFF CSV has no header")
    headers = [item.strip() for item in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise ValueError("UDiFF CSV contains duplicate column names")
    missing = sorted(REQUIRED_COLUMNS - set(headers))
    if missing:
        raise ValueError(f"UDiFF schema drift; missing required columns: {missing}")

    counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    trade_dates: set[str] = set()
    business_dates: set[str] = set()
    row_count = 0
    for row_number, raw_row in enumerate(reader, start=2):
        if None in raw_row:
            raise ValueError(f"UDiFF row {row_number} has more fields than the header")
        row = {key.strip(): (value or "").strip() for key, value in raw_row.items()}
        if not any(row.values()):
            continue
        row_count += 1
        for key in ("TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "TckrSymb", "XpryDt", "FinInstrmNm"):
            if not row.get(key):
                raise ValueError(f"blank required {key} at CSV row {row_number}")
        for field in ("TradDt", "BizDt", "XpryDt"):
            try:
                date.fromisoformat(row[field])
            except ValueError as exc:
                raise ValueError(
                    f"invalid {field} date at CSV row {row_number}: {row[field]!r}"
                ) from exc
        trade_dates.add(row["TradDt"])
        business_dates.add(row["BizDt"])
        if row["Sgmt"] != "FO" or row["Src"] != "NSE":
            raise ValueError(
                f"unexpected segment/source at CSV row {row_number}: "
                f"{row['Sgmt']}/{row['Src']}"
            )
        instrument_type = row["FinInstrmTp"]
        if instrument_type not in ALLOWED_INSTRUMENT_TYPES:
            raise ValueError(
                f"unknown FinInstrmTp {instrument_type!r} at CSV row {row_number}"
            )
        instrument_id = row["FinInstrmId"]
        if instrument_id in seen_ids:
            raise ValueError(f"duplicate FinInstrmId {instrument_id!r}")
        seen_ids.add(instrument_id)
        for column in NUMERIC_COLUMNS:
            _parse_number(row.get(column, ""), column, row_number)
        if instrument_type in {"STO", "IDO"}:
            if row.get("OptnTp") not in {"CE", "PE"}:
                raise ValueError(
                    f"invalid option type at CSV row {row_number}: {row.get('OptnTp')!r}"
                )
            if not row.get("StrkPric"):
                raise ValueError(f"option row missing strike at CSV row {row_number}")
        counts[instrument_type] += 1

    if row_count == 0:
        raise ValueError("UDiFF CSV contains no data rows")
    if trade_dates != {expected_trade_date}:
        raise ValueError(
            f"UDiFF TradDt {sorted(trade_dates)} does not match catalogue date "
            f"{expected_trade_date}"
        )
    if len(business_dates) != 1:
        raise ValueError(f"UDiFF contains multiple BizDt values: {sorted(business_dates)}")
    if not counts:
        raise ValueError("UDiFF contains no recognized derivative instrument types")
    return {
        "row_count": row_count,
        "trade_date": expected_trade_date,
        "business_dates": sorted(business_dates),
        "instrument_type_counts": dict(sorted(counts.items())),
        "distinct_contract_ids": len(seen_ids),
        "columns": headers,
        "unrecognized_columns": sorted(set(headers) - KNOWN_COLUMNS),
        "schema_sha256": hashlib.sha256(
            (",".join(headers) + "\n").encode("utf-8")
        ).hexdigest(),
    }


def ingest(root: Path = Path(".")) -> dict:
    catalogue = fetch_catalogue()
    selected = select_final_bhavcopy(catalogue)
    raw_zip = _get(
        selected["download_url"],
        REPORT_PAGE,
        "application/zip,application/octet-stream,*/*",
    )
    if not raw_zip:
        raise ValueError("NSE UDiFF archive download was empty")
    zip_hash = hashlib.sha256(raw_zip).hexdigest()
    member_name, csv_bytes = _read_zip(raw_zip)
    csv_hash = hashlib.sha256(csv_bytes).hexdigest()
    validation = validate_udiff_csv(csv_bytes, selected["trading_date"])
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    raw_dir = root / "data/raw/fno_bhavcopy"
    processed_dir = root / "data/processed/fno_bhavcopy"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    zip_path = raw_dir / (
        f"{selected['trading_date']}_{zip_hash[:16]}_{selected['filename']}"
    )
    csv_path = processed_dir / (
        f"nse_fo_udiff_{selected['trading_date']}_{zip_hash[:16]}.csv"
    )
    manifest = {
        "dataset": "nse_fo_udiff_final_bhavcopy",
        "publisher": "National Stock Exchange of India",
        "reference_url": REPORT_PAGE,
        **selected,
        "retrieved_at_utc": retrieved_at,
        "archive_member": member_name,
        "raw_archive_path": str(zip_path),
        "parsed_csv_path": str(csv_path),
        "raw_zip_bytes": len(raw_zip),
        "raw_zip_sha256": zip_hash,
        "csv_bytes": len(csv_bytes),
        "csv_sha256": csv_hash,
        "validation": validation,
        "finality": "final",
        "attribution_scope": "market_wide_contract_activity_not_participant_specific",
        "parser_version": 1,
    }
    # Raw source bytes are written before the parsed representation.
    if zip_path.exists() and hashlib.sha256(zip_path.read_bytes()).hexdigest() != zip_hash:
        raise RuntimeError(f"source archive changed for an existing immutable path: {zip_path}")
    if not zip_path.exists():
        zip_path.write_bytes(raw_zip)
    if csv_path.exists() and hashlib.sha256(csv_path.read_bytes()).hexdigest() != csv_hash:
        raise RuntimeError(f"parsed CSV changed for an existing trade date: {csv_path}")
    if not csv_path.exists():
        csv_path.write_bytes(csv_bytes)

    manifest_dir = raw_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{selected['trading_date']}_{zip_hash[:16]}.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("raw_zip_sha256") != zip_hash:
            raise RuntimeError(f"immutable manifest hash mismatch: {manifest_path}")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    current_manifest = raw_dir / "nse_fo_udiff_current_manifest.json"
    current_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "trade_date": selected["trading_date"],
        "row_count": validation["row_count"],
        "instrument_type_counts": validation["instrument_type_counts"],
        "raw_zip_sha256": zip_hash,
        "csv_sha256": csv_hash,
        "manifest_path": str(manifest_path),
        "raw_zip_path": str(zip_path),
        "csv_path": str(csv_path),
        "retrieved_at_utc": retrieved_at,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
