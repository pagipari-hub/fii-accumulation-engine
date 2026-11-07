"""Backfill current Nifty 500 cash, delivery and stock F&O history from NSE archives.

Historical membership and FPI attribution are deliberately not inferred.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from fii_engine.daily_stock_features import (
    FEATURE_COLUMNS,
    _aggregate_futures,
    _aggregate_options,
    _fmt,
    _number,
    _pct,
)
from fii_engine.historical_features import _load_daily_snapshots, build_historical_features
from fii_engine.nse_historical_archive_probe import (
    HEADERS,
    SOURCES,
    _decode_csv,
    inspect_payload,
)

USER_AGENT = HEADERS["User-Agent"]
DEFAULT_SESSIONS = 60
DEFAULT_MAX_CALENDAR_DAYS = 120
SNAPSHOT_NAME = re.compile(
    r"^daily_stock_features_(\d{4}-\d{2}-\d{2})_([0-9a-f]{16})\.csv$"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_universe(root: Path) -> tuple[list[dict], str]:
    path = root / "data/raw/universe/nifty500_current.csv"
    payload = path.read_bytes()
    digest = _sha256(payload)
    rows, fields = _decode_csv(payload, "current_nifty500_universe")
    required = {"symbol", "company_name", "industry", "series", "isin"}
    if not required.issubset(fields):
        raise ValueError(f"Nifty 500 universe schema missing: {sorted(required - set(fields))}")
    if not 480 <= len(rows) <= 520:
        raise ValueError(f"Nifty 500 universe row count outside safety range: {len(rows)}")
    symbols = [row["symbol"] for row in rows]
    isins = [row["isin"] for row in rows]
    if any(not item for item in symbols + isins):
        raise ValueError("Nifty 500 universe contains blank symbols or ISINs")
    if len(symbols) != len(set(symbols)) or len(isins) != len(set(isins)):
        raise ValueError("Nifty 500 universe symbols and ISINs must be unique")
    return rows, digest


def _download_archive(opener, source: str, trade_date: date) -> tuple[bytes | None, str]:
    spec = SOURCES[source]
    params = {
        "ymd": trade_date.strftime("%Y%m%d"),
        "ddmmyyyy": trade_date.strftime("%d%m%Y"),
    }
    url = spec["url"].format(**params)
    request = Request(
        url,
        headers={**HEADERS, "Referer": "https://www.nseindia.com/all-reports"},
    )
    for attempt in range(4):
        try:
            with opener.open(request, timeout=60) as response:
                if response.status != 200:
                    raise RuntimeError(f"{source}: unexpected HTTP {response.status}")
                payload = response.read()
                if not payload:
                    raise ValueError(f"{source}: empty archive payload")
                return payload, url
        except HTTPError as exc:
            if exc.code == 404:
                return None, url
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{source}: NSE archive HTTP {exc.code} for {trade_date}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{source}: archive request failed for {trade_date}: {exc}") from exc
    raise RuntimeError(f"{source}: archive failed after retries for {trade_date}")


def _csv_payload(payload: bytes, source: str) -> tuple[bytes, str | None]:
    if SOURCES[source]["kind"] == "csv":
        return payload, None
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"{source}: expected one CSV in ZIP, found {len(names)}")
            return archive.read(names[0]), names[0]
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{source}: archive is not a valid ZIP") from exc


def _save_raw(root: Path, trade_date: date, source: str, payload: bytes) -> dict:
    digest = _sha256(payload)
    suffix = ".zip" if SOURCES[source]["kind"] == "zip" else ".csv"
    path = (
        root / "data/raw/historical_backfill/raw" / trade_date.isoformat()
        / f"{source}_{digest[:16]}{suffix}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _sha256(path.read_bytes()) != digest:
        raise RuntimeError(f"immutable raw archive hash mismatch: {path}")
    if not path.exists():
        path.write_bytes(payload)
    return {
        "source": source,
        "url": SOURCES[source]["url"].format(
            ymd=trade_date.strftime("%Y%m%d"),
            ddmmyyyy=trade_date.strftime("%d%m%Y"),
        ),
        "raw_path": str(path.relative_to(root)),
        "raw_sha256": digest,
        "raw_bytes": len(payload),
    }


def _build_stock_rows(
    universe: list[dict],
    trade_date: date,
    cash_rows: list[dict],
    delivery_rows: list[dict],
    fo_rows: list[dict],
) -> list[dict]:
    cash_by_isin_series = {}
    for row in cash_rows:
        if row.get("FinInstrmTp") != "STK":
            continue
        isin, series = row.get("ISIN", ""), row.get("SctySrs", "")
        if not isin or not series:
            continue
        key = (isin, series)
        if key in cash_by_isin_series:
            raise ValueError(f"duplicate cash UDiFF ISIN/series for {trade_date}: {key}")
        cash_by_isin_series[key] = row

    delivery_by_symbol_series = {}
    for row in delivery_rows:
        symbol, series = row.get("SYMBOL", ""), row.get("SERIES", "")
        if not symbol or not series:
            continue
        key = (symbol, series)
        if key in delivery_by_symbol_series:
            raise ValueError(f"duplicate delivery symbol/series for {trade_date}: {key}")
        delivery_by_symbol_series[key] = row

    fo_by_symbol = defaultdict(list)
    for row in fo_rows:
        if row.get("FinInstrmTp") in {"STF", "STO"} and row.get("TckrSymb"):
            fo_by_symbol[row["TckrSymb"]].append(row)

    output = []
    for stock in universe:
        row = {field: "" for field in FEATURE_COLUMNS}
        symbol, isin, series = stock["symbol"], stock["isin"], stock["series"]
        row.update({
            "trade_date": trade_date.isoformat(),
            "symbol": symbol,
            "company_name": stock["company_name"],
            "industry": stock["industry"],
            "series": series,
            "isin": isin,
            # Historical point-in-time F&O membership is not inferred from today's list.
            "fno_status": "unknown",
            "deal_data_status": "not_collected_historical_backfill",
            "deal_counterparty_classification": "not_collected_historical_backfill",
            "quarterly_ownership_score_status": "not_collected_historical_backfill",
            "disclosed_deals_score_status": "not_collected_historical_backfill",
            "cash_market_score_status": "not_scored",
            "stock_derivatives_score_status": "not_scored",
            "cash_market_score_reason": "historical_backfill_excludes_deals_and_ownership",
            "stock_derivatives_score_reason": "historical_fno_membership_not_available",
        })

        cash = cash_by_isin_series.get((isin, series))
        if cash is None:
            row["cash_data_status"] = "not_found_in_archive"
        else:
            if cash.get("TradDt") != trade_date.isoformat():
                raise ValueError(f"cash UDiFF date mismatch for {symbol}/{trade_date}")
            close = _number(cash.get("ClsPric"), field=f"{symbol} historical cash close")
            previous = _number(cash.get("PrvsClsgPric"), field=f"{symbol} historical previous close")
            row.update({
                "cash_data_status": "observed",
                "cash_close": _fmt(close),
                "cash_previous_close": _fmt(previous),
                "cash_return_pct": _fmt(_pct(close, previous)),
                "cash_volume": _fmt(_number(cash.get("TtlTradgVol"), field=f"{symbol} cash volume", integer=True)),
                "cash_turnover": _fmt(_number(cash.get("TtlTrfVal"), field=f"{symbol} cash turnover")),
                "cash_trade_count": _fmt(_number(cash.get("TtlNbOfTxsExctd"), field=f"{symbol} cash trades", integer=True)),
            })

        delivery = delivery_by_symbol_series.get((symbol, series))
        if delivery is None:
            row["delivery_status"] = "not_found"
        else:
            delivery_quantity = _number(delivery.get("DELIV_QTY"), field=f"{symbol} delivery quantity", integer=True)
            delivery_percentage = _number(delivery.get("DELIV_PER"), field=f"{symbol} delivery percentage")
            if delivery_quantity is not None and delivery_percentage is not None:
                row["delivery_status"] = "reported"
                row["delivery_quantity"] = _fmt(delivery_quantity)
                row["delivery_percentage"] = _fmt(delivery_percentage)
            else:
                row["delivery_status"] = "not_reported"

        contracts = fo_by_symbol.get(symbol, [])
        futures = _aggregate_futures(contracts, trade_date.isoformat()) if contracts else None
        options = _aggregate_options(contracts, trade_date.isoformat()) if contracts else None
        if futures is None:
            row["futures_data_status"] = "no_contract_observation"
        else:
            row.update({
                "futures_data_status": "observed",
                "futures_expiry": futures["expiry"],
                "futures_close": futures["close"],
                "futures_previous_close": futures["previous_close"],
                "futures_return_pct": futures["return_pct"],
                "futures_open_interest": futures["open_interest"],
                "futures_oi_change": futures["oi_change"],
                "futures_oi_change_pct": futures["oi_change_pct"],
                "futures_volume": futures["volume"],
                "futures_buildup": futures["buildup"],
            })
        if options is None:
            row["options_data_status"] = "no_contract_observation"
        else:
            row.update({
                "options_data_status": "observed",
                "options_expiry": options["expiry"],
                "call_open_interest": options["call_oi"],
                "put_open_interest": options["put_oi"],
                "options_pcr_oi": options["pcr_oi"],
                "call_volume": options["call_volume"],
                "put_volume": options["put_volume"],
                "options_pcr_volume": options["pcr_volume"],
            })
        output.append(row)

    if len(output) != len(universe):
        raise ValueError(f"historical matrix row count mismatch on {trade_date}")
    if len({row["symbol"] for row in output}) != len(output):
        raise ValueError(f"duplicate symbols in historical matrix on {trade_date}")
    return output


def _write_snapshot(root: Path, trade_date: date, rows: list[dict]) -> dict:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(FEATURE_COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode("utf-8")
    digest = _sha256(payload)
    path = root / "data/snapshots/daily_stock_features" / (
        f"daily_stock_features_{trade_date.isoformat()}_{digest[:16]}.csv"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _sha256(path.read_bytes()) != digest:
        raise RuntimeError(f"immutable daily snapshot collision: {path}")
    if not path.exists():
        path.write_bytes(payload)
    return {
        "trade_date": trade_date.isoformat(),
        "snapshot_path": str(path.relative_to(root)),
        "snapshot_sha256": digest,
        "row_count": len(rows),
    }


def run_backfill(
    root: Path = Path("."),
    sessions: int = DEFAULT_SESSIONS,
    max_calendar_days: int = DEFAULT_MAX_CALENDAR_DAYS,
    as_of_date: str | None = None,
) -> dict:
    if sessions < 1 or sessions > 120:
        raise ValueError("sessions must be between 1 and 120")
    if max_calendar_days < sessions or max_calendar_days > 365:
        raise ValueError("max_calendar_days must be between sessions and 365")
    root = root.resolve()
    universe, universe_hash = _load_universe(root)
    existing_snapshots, existing_dates_list = _load_daily_snapshots(root)
    existing_dates = set(existing_dates_list)
    if as_of_date:
        as_of = date.fromisoformat(as_of_date)
    elif existing_dates:
        as_of = max(date.fromisoformat(item) for item in existing_dates)
    else:
        as_of = date.today()
    if existing_dates and as_of < max(date.fromisoformat(item) for item in existing_dates):
        raise ValueError("as_of_date cannot be earlier than the latest existing daily snapshot")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    raw_refs = []
    source_days = []
    unavailable_days = []
    created_snapshots = []
    included_dates = set(existing_dates)
    candidate = as_of
    days_examined = 0

    while len(included_dates) < sessions and days_examined < max_calendar_days:
        days_examined += 1
        if candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
            continue
        date_text = candidate.isoformat()
        if date_text in included_dates:
            candidate -= timedelta(days=1)
            continue

        cash_payload, cash_url = _download_archive(opener, "cash_udiff", candidate)
        if cash_payload is None:
            unavailable_days.append({
                "trade_date": date_text,
                "status": "no_cash_archive",
                "url": cash_url,
            })
            candidate -= timedelta(days=1)
            continue
        cash_inspection = inspect_payload(cash_payload, "cash_udiff", date_text)

        delivery_payload, delivery_url = _download_archive(opener, "cash_delivery_full", candidate)
        fo_payload, fo_url = _download_archive(opener, "fo_udiff", candidate)
        if delivery_payload is None or fo_payload is None:
            unavailable_days.append({
                "trade_date": date_text,
                "status": "incomplete_source_set",
                "cash_delivery_available": delivery_payload is not None,
                "fo_available": fo_payload is not None,
                "cash_delivery_url": delivery_url,
                "fo_url": fo_url,
            })
            candidate -= timedelta(days=1)
            continue

        delivery_inspection = inspect_payload(delivery_payload, "cash_delivery_full", date_text)
        fo_inspection = inspect_payload(fo_payload, "fo_udiff", date_text)
        cash_csv, _ = _csv_payload(cash_payload, "cash_udiff")
        delivery_csv, _ = _csv_payload(delivery_payload, "cash_delivery_full")
        fo_csv, _ = _csv_payload(fo_payload, "fo_udiff")
        cash_rows, _ = _decode_csv(cash_csv, "cash_udiff")
        delivery_rows, _ = _decode_csv(delivery_csv, "cash_delivery_full")
        fo_rows, _ = _decode_csv(fo_csv, "fo_udiff")

        # Validate all three source datasets before creating any normalized output.
        source_refs = []
        for source, payload, url, inspection in (
            ("cash_udiff", cash_payload, cash_url, cash_inspection),
            ("cash_delivery_full", delivery_payload, delivery_url, delivery_inspection),
            ("fo_udiff", fo_payload, fo_url, fo_inspection),
        ):
            ref = _save_raw(root, candidate, source, payload)
            ref["url"] = url
            ref["row_count"] = inspection["row_count"]
            ref["columns"] = inspection["columns"]
            source_refs.append(ref)
            raw_refs.append(ref)

        rows = _build_stock_rows(universe, candidate, cash_rows, delivery_rows, fo_rows)
        snapshot = _write_snapshot(root, candidate, rows)
        snapshot["source_refs"] = source_refs
        created_snapshots.append(snapshot)
        source_days.append({
            "trade_date": date_text,
            "status": "complete",
            "universe_count": len(universe),
            "cash_udiff_rows": cash_inspection["row_count"],
            "cash_delivery_rows": delivery_inspection["row_count"],
            "fo_udiff_rows": fo_inspection["row_count"],
            "snapshot_path": snapshot["snapshot_path"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "source_refs": source_refs,
        })
        included_dates.add(date_text)
        candidate -= timedelta(days=1)

    sorted_dates = sorted(included_dates)
    status = "complete" if len(included_dates) >= sessions else "insufficient_archive_coverage"
    history = None
    if created_snapshots:
        history = build_historical_features(root)

    manifest = {
        "dataset": "nse_official_historical_stock_evidence_backfill",
        "publisher": "National Stock Exchange of India",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "target_sessions": sessions,
        "sessions_available": len(included_dates),
        "max_calendar_days_examined": days_examined,
        "max_calendar_days_limit": max_calendar_days,
        "as_of_date": as_of.isoformat(),
        "trade_dates": sorted_dates,
        "current_universe_count": len(universe),
        "current_universe_sha256": universe_hash,
        "universe_basis": (
            "Current Nifty 500 constituent snapshot applied retrospectively. "
            "This is not point-in-time historical index membership and must not "
            "be used for survivorship-bias-free backtesting."
        ),
        "historical_fno_membership": (
            "unknown; daily contract observations are recorded where present, "
            "but today's F&O list is not projected backward"
        ),
        "stock_specific_attribution_boundary": (
            "Cash/delivery and derivatives observations describe market activity; "
            "they do not identify FII/FPI ownership or participant-by-stock positions."
        ),
        "created_snapshots": created_snapshots,
        "unavailable_dates": unavailable_days,
        "source_refs": raw_refs,
        "history_build": {
            "as_of_trade_date": history["as_of_trade_date"],
            "daily_stock_session_count": history["daily_stock_session_count"],
            "market_flow_session_count": history["market_flow_session_count"],
            "participant_oi_session_count": history["participant_oi_session_count"],
            "participant_volume_session_count": history["participant_volume_session_count"],
        } if history else None,
    }
    output = root / "data/raw/historical_backfill"
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "backfill_manifest_current.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if status != "complete":
        raise RuntimeError(
            f"historical backfill incomplete: {len(included_dates)}/{sessions} "
            f"sessions within {max_calendar_days} calendar days"
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    parser.add_argument("--max-calendar-days", type=int, default=DEFAULT_MAX_CALENDAR_DAYS)
    parser.add_argument("--as-of-date")
    args = parser.parse_args()
    print(json.dumps(
        run_backfill(
            args.repo_root,
            sessions=args.sessions,
            max_calendar_days=args.max_calendar_days,
            as_of_date=args.as_of_date,
        ),
        indent=2,
    ))


if __name__ == "__main__":
    main()
