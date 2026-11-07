"""Build a provenance-backed daily stock evidence matrix for the Nifty 500.

This is an input/evidence ledger, not an FPI attribution or ranking module.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


FEATURE_COLUMNS = (
    "trade_date", "symbol", "company_name", "industry", "isin", "fno_status",
    "cash_data_status", "cash_close", "cash_previous_close", "cash_return_pct",
    "cash_volume", "cash_turnover", "cash_trade_count",
    "delivery_status", "delivery_quantity", "delivery_percentage",
    "futures_data_status", "futures_expiry", "futures_close", "futures_previous_close",
    "futures_return_pct", "futures_open_interest", "futures_oi_change",
    "futures_oi_change_pct", "futures_volume", "futures_buildup",
    "options_data_status", "options_expiry", "call_open_interest", "put_open_interest",
    "options_pcr_oi", "call_volume", "put_volume", "options_pcr_volume",
    "deal_data_status", "deal_event_count", "deal_buy_quantity", "deal_sell_quantity",
    "deal_distinct_client_count", "deal_trade_dates", "deal_counterparty_classification",
    "quarterly_ownership_score_status", "disclosed_deals_score_status",
    "cash_market_score_status", "stock_derivatives_score_status",
    "cash_market_score_reason", "stock_derivatives_score_reason",
)

REQUIRED_CASH_COLUMNS = {
    "FinInstrmTp", "ISIN", "TckrSymb", "SctySrs", "ClsPric", "PrvsClsgPric",
    "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "TradDt",
}
REQUIRED_FO_COLUMNS = {
    "FinInstrmTp", "TckrSymb", "XpryDt", "OptnTp", "ClsPric", "PrvsClsgPric",
    "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "TradDt",
}
REQUIRED_DEAL_COLUMNS = {
    "trade_date", "deal_type", "symbol", "client_name", "side", "quantity",
    "record_fingerprint",
}
VALID_FNO_STATUS = {"eligible", "not_eligible", "unknown"}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"manifest path escapes repository root: {relative}")
    return path


def _read_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid source manifest: {path}") from exc


def _load_source_csv(root: Path, relative: str, expected_hash: str | None = None) -> tuple[list[dict], str]:
    path = _safe_path(root, relative)
    if not path.is_file():
        raise FileNotFoundError(f"manifest source file missing: {relative}")
    payload = path.read_bytes()
    digest = _sha256(payload)
    if expected_hash and digest != expected_hash:
        raise ValueError(f"source hash mismatch: {relative}")
    try:
        reader = csv.DictReader(payload.decode("utf-8-sig").splitlines())
        if not reader.fieldnames:
            raise ValueError(f"source CSV has no header: {relative}")
        headers = [header.strip() for header in reader.fieldnames]
        rows = [
            {headers[i]: (value or "").strip() for i, value in enumerate(row.values())}
            for row in reader
        ]
    except UnicodeDecodeError as exc:
        raise ValueError(f"source CSV is not UTF-8: {relative}") from exc
    return rows, digest


def _read_direct_csv(path: Path, required: set[str]) -> tuple[list[dict], str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = path.read_bytes()
    digest = _sha256(payload)
    try:
        reader = csv.DictReader(payload.decode("utf-8-sig").splitlines())
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        headers = [header.strip() for header in reader.fieldnames]
        if not required.issubset(set(headers)):
            raise ValueError(
                f"CSV schema drift at {path}: missing {sorted(required - set(headers))}"
            )
        rows = [
            {headers[i]: (value or "").strip() for i, value in enumerate(row.values())}
            for row in reader
        ]
    except UnicodeDecodeError as exc:
        raise ValueError(f"source CSV is not UTF-8: {path}") from exc
    return rows, digest


def _number(value: object, *, field: str, integer: bool = False) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "NA", "N/A", "null"}:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value for {field}: {text!r}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite numeric value for {field}")
    if integer and number != number.to_integral_value():
        raise ValueError(f"non-integer value for {field}: {text!r}")
    return number


def _fmt(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _pct(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - Decimal(1)) * Decimal(100)


def _read_universe(root: Path) -> tuple[list[dict], str]:
    path = root / "data/raw/universe/nifty500_current.csv"
    rows, digest = _read_direct_csv(
        path, {"symbol", "company_name", "industry", "series", "isin"}
    )
    if not 480 <= len(rows) <= 520:
        raise ValueError(f"Nifty 500 universe row count outside safety range: {len(rows)}")
    symbols = [row["symbol"] for row in rows]
    isins = [row["isin"] for row in rows]
    if any(not value for value in symbols + isins):
        raise ValueError("Nifty 500 universe contains blank symbol or ISIN")
    if len(symbols) != len(set(symbols)) or len(isins) != len(set(isins)):
        raise ValueError("Nifty 500 universe symbols and ISINs must be unique")
    return rows, digest


def _load_fno_status(root: Path, universe: list[dict]) -> tuple[dict, dict]:
    path = root / "data/raw/fno_universe/nifty500_fno_status_current.csv"
    manifest_path = root / "data/raw/fno_universe/nifty500_fno_status_current_manifest.json"
    if not path.is_file():
        return {}, {"status": "unavailable", "reason": "current F&O status file missing"}
    rows, digest = _read_direct_csv(
        path, {"symbol", "isin", "fno_status", "matched_fno_symbol"}
    )
    manifest = _read_manifest(manifest_path)
    if manifest and manifest.get("output_sha256") and manifest["output_sha256"] != digest:
        raise ValueError("F&O status current manifest hash does not match current CSV")
    by_symbol = {}
    for row in rows:
        status = row["fno_status"]
        if status not in VALID_FNO_STATUS:
            raise ValueError(f"invalid F&O status for {row['symbol']}: {status}")
        if row["symbol"] in by_symbol:
            raise ValueError(f"duplicate F&O status symbol: {row['symbol']}")
        by_symbol[row["symbol"]] = row
    expected = {row["symbol"] for row in universe}
    expected_isins = {row["symbol"]: row["isin"] for row in universe}
    for symbol, row in by_symbol.items():
        if row["isin"] != expected_isins.get(symbol):
            raise ValueError(f"F&O status ISIN mismatch for {symbol}")
    if set(by_symbol) != expected:
        raise ValueError(
            f"F&O status universe mismatch: missing={sorted(expected - set(by_symbol))[:10]}, "
            f"extra={sorted(set(by_symbol) - expected)[:10]}"
        )
    return by_symbol, {
        "status": "available",
        "path": str(path.relative_to(root)),
        "sha256": digest,
        "retrieved_at_utc": manifest.get("fno_membership_retrieved_at_utc") if manifest else None,
        "effective_date": manifest.get("fno_membership_effective_date") if manifest else None,
    }


def _load_cash_sources(root: Path) -> tuple[dict, dict, dict]:
    manifest_path = root / "data/raw/cash_market/nse_cm_cash_current_manifest.json"
    manifest = _read_manifest(manifest_path)
    if not manifest:
        return {}, {}, {"status": "unavailable", "reason": "cash-market current manifest missing"}
    if manifest.get("finality") != "final":
        return {}, {}, {"status": "unavailable", "reason": f"cash-market source is not final: {manifest.get('finality')}"}
    datasets = manifest.get("datasets", [])
    udiff = next((item for item in datasets if item.get("parsed_path")), None)
    delivery = next((item for item in datasets if item.get("parsed_path") is None), None)
    if not udiff or not delivery:
        return {}, {}, {"status": "unavailable", "reason": "cash manifest lacks UDiFF or delivery dataset"}
    if udiff.get("trade_date") != delivery.get("trade_date") or udiff.get("trade_date") != manifest.get("trade_date"):
        raise ValueError("cash UDiFF and delivery dates do not agree")
    cash_rows, cash_hash = _load_source_csv(root, udiff["parsed_path"], udiff.get("parsed_sha256"))
    delivery_rows, delivery_hash = _load_source_csv(root, delivery["raw_path"], delivery.get("raw_sha256"))
    if not REQUIRED_CASH_COLUMNS.issubset(cash_rows[0] if cash_rows else {}):
        raise ValueError("cash UDiFF schema is missing required columns")
    cash_by_isin = {}
    for row in cash_rows:
        if row.get("FinInstrmTp") != "STK" or row.get("SctySrs") != "EQ":
            continue
        isin = row.get("ISIN", "")
        if not isin:
            continue
        if isin in cash_by_isin:
            raise ValueError(f"duplicate EQ cash-market ISIN: {isin}")
        cash_by_isin[isin] = row

    delivery_by_symbol = {}
    for row in delivery_rows:
        if row.get("SERIES", "").strip() != "EQ":
            continue
        symbol = row.get("SYMBOL", "").strip()
        if not symbol:
            continue
        if symbol in delivery_by_symbol:
            raise ValueError(f"duplicate EQ delivery symbol: {symbol}")
        delivery_by_symbol[symbol] = row

    trade_date = manifest["trade_date"]
    for row in cash_by_isin.values():
        if row.get("TradDt") != trade_date:
            raise ValueError("cash UDiFF row date does not match current manifest")
    for row in delivery_by_symbol.values():
        row_date = row.get("DATE1", "").strip()
        if row_date and date.fromisoformat(trade_date).strftime("%d-%b-%Y").upper() != row_date.upper():
            raise ValueError("delivery row date does not match current manifest")
    return cash_by_isin, delivery_by_symbol, {
        "status": "available",
        "trade_date": trade_date,
        "retrieved_at_utc": manifest.get("retrieved_at_utc"),
        "udiff_path": udiff["parsed_path"],
        "udiff_sha256": cash_hash,
        "delivery_path": delivery["raw_path"],
        "delivery_sha256": delivery_hash,
        "manifest_path": str(manifest_path.relative_to(root)),
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "cash_eq_rows": len(cash_by_isin),
        "delivery_eq_rows": len(delivery_by_symbol),
    }


def _load_fo_sources(root: Path) -> tuple[dict, dict]:
    manifest_path = root / "data/raw/fno_bhavcopy/nse_fo_udiff_current_manifest.json"
    manifest = _read_manifest(manifest_path)
    if not manifest:
        return {}, {"status": "unavailable", "reason": "F&O bhavcopy current manifest missing"}
    relative = manifest.get("parsed_csv_path")
    if not relative:
        return {}, {"status": "unavailable", "reason": "F&O bhavcopy manifest has no parsed CSV"}
    rows, digest = _load_source_csv(root, relative, manifest.get("csv_sha256"))
    if not REQUIRED_FO_COLUMNS.issubset(rows[0] if rows else {}):
        raise ValueError("F&O bhavcopy schema is missing required columns")
    trade_date = manifest.get("trading_date")
    if not trade_date or any(row.get("TradDt") != trade_date for row in rows):
        raise ValueError("F&O bhavcopy row dates do not match current manifest")
    if manifest.get("finality") != "final":
        return {}, {
            "status": "unavailable",
            "reason": f"F&O bhavcopy is not final: {manifest.get('finality')}",
            "trade_date": trade_date,
        }
    by_symbol = defaultdict(list)
    for row in rows:
        if row.get("FinInstrmTp") in {"STF", "STO"}:
            symbol = row.get("TckrSymb", "")
            if symbol:
                by_symbol[symbol].append(row)
    return by_symbol, {
        "status": "available",
        "trade_date": trade_date,
        "retrieved_at_utc": manifest.get("retrieved_at_utc"),
        "path": relative,
        "sha256": digest,
        "manifest_path": str(manifest_path.relative_to(root)),
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "attribution_scope": manifest.get("attribution_scope"),
    }


def _load_deal_sources(root: Path, trade_date: str | None) -> tuple[dict, dict]:
    manifest_path = root / "data/raw/bulk_block_deals/current_manifest.json"
    manifest = _read_manifest(manifest_path)
    if not manifest:
        return {}, {"status": "unavailable", "reason": "bulk/block deal current manifest missing"}
    events_by_symbol = defaultdict(list)
    seen = {}
    snapshot_refs = []
    for dataset in manifest.get("datasets", []):
        for snapshot in dataset.get("snapshots", []):
            relative = snapshot.get("snapshot_path")
            if not relative:
                continue
            rows, digest = _load_source_csv(root, relative, snapshot.get("snapshot_sha256"))
            if not REQUIRED_DEAL_COLUMNS.issubset(rows[0] if rows else set()):
                if rows:
                    raise ValueError(f"deal snapshot schema drift: {relative}")
            snapshot_refs.append({
                "path": relative,
                "sha256": digest,
                "trade_date": snapshot.get("trade_date"),
                "row_count": len(rows),
            })
            for row in rows:
                fingerprint = row["record_fingerprint"]
                previous = seen.get(fingerprint)
                if previous is not None:
                    if previous != row:
                        raise ValueError(f"conflicting duplicate deal fingerprint: {fingerprint}")
                    continue
                seen[fingerprint] = row
                symbol = row["symbol"]
                if symbol:
                    events_by_symbol[symbol].append(row)
    requested_from = manifest.get("requested_from")
    requested_to = manifest.get("requested_to")
    target_date = trade_date or requested_to
    for symbol, events in list(events_by_symbol.items()):
        events_by_symbol[symbol] = [
            row for row in events
            if row.get("trade_date")
            and (not requested_from or row["trade_date"] >= requested_from)
            and (not target_date or row["trade_date"] <= target_date)
        ]
    return events_by_symbol, {
        "status": "available",
        "requested_from": requested_from,
        "requested_to": requested_to,
        "current_for_trade_date": bool(target_date and requested_to == target_date),
        "retrieved_at_utc": manifest.get("retrieved_at_utc"),
        "manifest_path": str(manifest_path.relative_to(root)),
        "manifest_sha256": _sha256(manifest_path.read_bytes()),
        "unique_event_count": len(seen),
        "snapshot_refs": snapshot_refs,
        "counterparty_classification": "unclassified_not_FPI_attributed",
    }


def _aggregate_futures(rows: list[dict], trade_date: str) -> dict | None:
    futures = [row for row in rows if row.get("FinInstrmTp") == "STF"]
    dated = []
    for row in futures:
        expiry = row.get("XpryDt", "")
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError:
            continue
        if expiry_date >= date.fromisoformat(trade_date):
            dated.append((expiry_date, row))
    if not dated:
        return None
    nearest = min(item[0] for item in dated)
    selected = [row for expiry, row in dated if expiry == nearest]
    if len(selected) != 1:
        raise ValueError(f"multiple stock futures for one symbol/expiry: {nearest}")
    row = selected[0]
    close = _number(row["ClsPric"], field="futures close")
    previous = _number(row["PrvsClsgPric"], field="futures previous close")
    oi = _number(row["OpnIntrst"], field="futures open interest", integer=True)
    oi_change = _number(row["ChngInOpnIntrst"], field="futures OI change", integer=True)
    volume = _number(row["TtlTradgVol"], field="futures volume", integer=True)
    previous_oi = oi - oi_change if oi is not None and oi_change is not None else None
    if previous_oi is not None and previous_oi < 0:
        raise ValueError("futures OI change implies a negative previous open interest")
    oi_change_pct = (
        oi_change / previous_oi * Decimal(100)
        if oi_change is not None and previous_oi is not None and previous_oi > 0
        else None
    )
    price_change = _pct(close, previous)
    if oi == 0 and oi_change == 0:
        buildup = "no_open_interest"
    elif price_change is None or oi_change is None:
        buildup = "unclassified_missing_metric"
    elif price_change > 0 and oi_change > 0:
        buildup = "long_buildup"
    elif price_change < 0 and oi_change > 0:
        buildup = "short_buildup"
    elif price_change > 0 and oi_change < 0:
        buildup = "short_covering"
    elif price_change < 0 and oi_change < 0:
        buildup = "long_unwinding"
    else:
        buildup = "neutral"
    return {
        "expiry": nearest.isoformat(),
        "close": _fmt(close),
        "previous_close": _fmt(previous),
        "return_pct": _fmt(price_change),
        "open_interest": _fmt(oi),
        "oi_change": _fmt(oi_change),
        "oi_change_pct": _fmt(oi_change_pct),
        "volume": _fmt(volume),
        "buildup": buildup,
    }


def _aggregate_options(rows: list[dict], trade_date: str) -> dict | None:
    options = [row for row in rows if row.get("FinInstrmTp") == "STO"]
    dated = []
    for row in options:
        try:
            expiry = date.fromisoformat(row.get("XpryDt", ""))
        except ValueError:
            continue
        if expiry >= date.fromisoformat(trade_date):
            dated.append((expiry, row))
    if not dated:
        return None
    nearest = min(item[0] for item in dated)
    selected = [row for expiry, row in dated if expiry == nearest]
    sums = {
        "CE": {"oi": Decimal(0), "oi_change": Decimal(0), "volume": Decimal(0)},
        "PE": {"oi": Decimal(0), "oi_change": Decimal(0), "volume": Decimal(0)},
    }
    present = set()
    for row in selected:
        option_type = row.get("OptnTp")
        if option_type not in sums:
            raise ValueError(f"unknown stock option type: {option_type!r}")
        present.add(option_type)
        for key, field in (
            ("oi", "OpnIntrst"),
            ("oi_change", "ChngInOpnIntrst"),
            ("volume", "TtlTradgVol"),
        ):
            value = _number(row[field], field=f"options {field}", integer=True)
            if value is not None:
                sums[option_type][key] += value
    call_oi, put_oi = sums["CE"]["oi"], sums["PE"]["oi"]
    call_volume, put_volume = sums["CE"]["volume"], sums["PE"]["volume"]
    return {
        "expiry": nearest.isoformat(),
        "call_oi": _fmt(call_oi) if "CE" in present else None,
        "put_oi": _fmt(put_oi) if "PE" in present else None,
        "pcr_oi": _fmt(put_oi / call_oi) if "CE" in present and "PE" in present and call_oi > 0 else None,
        "call_volume": _fmt(call_volume) if "CE" in present else None,
        "put_volume": _fmt(put_volume) if "PE" in present else None,
        "pcr_volume": _fmt(put_volume / call_volume) if "CE" in present and "PE" in present and call_volume > 0 else None,
    }


def _deal_summary(events: list[dict]) -> dict:
    buy = Decimal(0)
    sell = Decimal(0)
    clients = set()
    dates = set()
    for row in events:
        quantity = _number(row.get("quantity"), field="deal quantity", integer=True)
        if quantity is not None:
            if row.get("side", "").upper() == "BUY":
                buy += quantity
            elif row.get("side", "").upper() == "SELL":
                sell += quantity
        if row.get("client_name"):
            clients.add(row["client_name"])
        if row.get("trade_date"):
            dates.add(row["trade_date"])
    return {
        "count": len(events),
        "buy_quantity": _fmt(buy),
        "sell_quantity": _fmt(sell),
        "client_count": len(clients),
        "dates": ";".join(sorted(dates)),
    }


def build_daily_features(root: Path, collector_status: dict | None = None) -> dict:
    root = root.resolve()
    universe, universe_hash = _read_universe(root)
    if collector_status is None:
        fno_status, fno_meta = _load_fno_status(root, universe)
        cash_by_isin, delivery_by_symbol, cash_meta = _load_cash_sources(root)
        fo_by_symbol, fo_meta = _load_fo_sources(root)
        deals_by_symbol, deals_meta = _load_deal_sources(root, None)
    else:
        fno_prerequisites = ("nifty500_universe", "nse_fo_underlyings", "nifty500_fno_status")
        if all(collector_status.get(name, {}).get("status") == "success" for name in fno_prerequisites):
            fno_status, fno_meta = _load_fno_status(root, universe)
        else:
            fno_status = {}
            fno_meta = {"status": "unavailable", "reason": "one or more F&O universe collectors failed"}
        if collector_status.get("nse_cash_market", {}).get("status") == "success":
            cash_by_isin, delivery_by_symbol, cash_meta = _load_cash_sources(root)
        else:
            cash_by_isin, delivery_by_symbol = {}, {}
            cash_meta = {"status": "unavailable", "reason": "NSE cash-market collector did not succeed"}
        if collector_status.get("nse_fo_bhavcopy", {}).get("status") == "success":
            fo_by_symbol, fo_meta = _load_fo_sources(root)
        else:
            fo_by_symbol = {}
            fo_meta = {"status": "unavailable", "reason": "NSE F&O bhavcopy collector did not succeed"}
        if collector_status.get("nse_bulk_block_deals", {}).get("status") == "success":
            deals_by_symbol, deals_meta = _load_deal_sources(root, None)
        else:
            deals_by_symbol = {}
            deals_meta = {"status": "unavailable", "reason": "NSE bulk/block deals collector did not succeed"}
    trade_date = cash_meta.get("trade_date") or fo_meta.get("trade_date")
    if cash_meta.get("trade_date") and fo_meta.get("trade_date") and cash_meta["trade_date"] != fo_meta["trade_date"]:
        fo_meta = {**fo_meta, "status": "unavailable", "reason": "cash and F&O trading dates do not match"}
        fo_by_symbol = {}
    if deals_meta.get("status") == "available" and trade_date and deals_meta.get("requested_to") != trade_date:
        deals_by_symbol, deals_meta = _load_deal_sources(root, trade_date)

    output_rows = []
    counts = defaultdict(int)
    for stock in universe:
        symbol, isin = stock["symbol"], stock["isin"]
        fno_row = fno_status.get(symbol)
        fno_state = fno_row["fno_status"] if fno_row else "unknown"
        cash = cash_by_isin.get(isin)
        delivery = delivery_by_symbol.get(symbol)
        if cash:
            cash_status = "observed"
            cash_close = _number(cash.get("ClsPric"), field="cash close")
            cash_previous = _number(cash.get("PrvsClsgPric"), field="cash previous close")
            cash_return = _pct(cash_close, cash_previous)
            cash_volume = _number(cash.get("TtlTradgVol"), field="cash volume", integer=True)
            cash_turnover = _number(cash.get("TtlTrfVal"), field="cash turnover")
            cash_trades = _number(cash.get("TtlNbOfTxsExctd"), field="cash trade count", integer=True)
        else:
            cash_status = "unavailable"
            cash_close = cash_previous = cash_return = cash_volume = cash_turnover = cash_trades = None

        if delivery:
            delivery_quantity = _number(delivery.get("DELIV_QTY"), field="delivery quantity", integer=True)
            delivery_percentage = _number(delivery.get("DELIV_PER"), field="delivery percentage")
            if delivery_percentage is not None:
                delivery_status = "reported"
            elif delivery_quantity is None:
                delivery_status = "not_reported"
            else:
                delivery_status = "not_reported_with_trades"
        else:
            delivery_quantity = delivery_percentage = None
            delivery_status = "not_found"

        if fno_state == "not_eligible":
            futures_status = options_status = "not_applicable"
            futures = options = None
        elif fno_state == "eligible" and fo_meta.get("status") == "available":
            fno_symbol = (fno_row.get("matched_fno_symbol") or symbol) if fno_row else symbol
            source_rows = fo_by_symbol.get(fno_symbol, [])
            futures = _aggregate_futures(source_rows, fo_meta["trade_date"])
            options = _aggregate_options(source_rows, fo_meta["trade_date"])
            futures_status = "observed" if futures else "unavailable"
            options_status = "observed" if options else "unavailable"
        else:
            futures_status = options_status = "unavailable"
            futures = options = None

        events = deals_by_symbol.get(symbol, [])
        deal_summary = _deal_summary(events)
        if events:
            deal_status = (
                "observed_events" if deals_meta.get("current_for_trade_date")
                else "stale_window_observed_events"
            )
        elif deals_meta.get("status") == "available":
            deal_status = (
                "no_record_in_available_snapshots" if deals_meta.get("current_for_trade_date")
                else "stale_window_no_record"
            )
        else:
            deal_status = "unavailable"

        row = {
            "trade_date": trade_date,
            "symbol": symbol,
            "company_name": stock["company_name"],
            "industry": stock["industry"],
            "isin": isin,
            "fno_status": fno_state,
            "cash_data_status": cash_status,
            "cash_close": _fmt(cash_close),
            "cash_previous_close": _fmt(cash_previous),
            "cash_return_pct": _fmt(cash_return),
            "cash_volume": _fmt(cash_volume),
            "cash_turnover": _fmt(cash_turnover),
            "cash_trade_count": _fmt(cash_trades),
            "delivery_status": delivery_status,
            "delivery_quantity": _fmt(delivery_quantity),
            "delivery_percentage": _fmt(delivery_percentage),
            "futures_data_status": futures_status,
            "futures_expiry": futures.get("expiry") if futures else None,
            "futures_close": futures.get("close") if futures else None,
            "futures_previous_close": futures.get("previous_close") if futures else None,
            "futures_return_pct": futures.get("return_pct") if futures else None,
            "futures_open_interest": futures.get("open_interest") if futures else None,
            "futures_oi_change": futures.get("oi_change") if futures else None,
            "futures_oi_change_pct": futures.get("oi_change_pct") if futures else None,
            "futures_volume": futures.get("volume") if futures else None,
            "futures_buildup": futures.get("buildup") if futures else None,
            "options_data_status": options_status,
            "options_expiry": options.get("expiry") if options else None,
            "call_open_interest": options.get("call_oi") if options else None,
            "put_open_interest": options.get("put_oi") if options else None,
            "options_pcr_oi": options.get("pcr_oi") if options else None,
            "call_volume": options.get("call_volume") if options else None,
            "put_volume": options.get("put_volume") if options else None,
            "options_pcr_volume": options.get("pcr_volume") if options else None,
            "deal_data_status": deal_status,
            "deal_event_count": deal_summary["count"] if deals_meta.get("status") == "available" else None,
            "deal_buy_quantity": deal_summary["buy_quantity"] if deals_meta.get("status") == "available" else None,
            "deal_sell_quantity": deal_summary["sell_quantity"] if deals_meta.get("status") == "available" else None,
            "deal_distinct_client_count": deal_summary["client_count"] if deals_meta.get("status") == "available" else None,
            "deal_trade_dates": deal_summary["dates"] if deals_meta.get("status") == "available" else None,
            "deal_counterparty_classification": "unclassified_not_FPI_attributed",
            "quarterly_ownership_score_status": "unavailable",
            "disclosed_deals_score_status": "unavailable",
            "cash_market_score_status": "unavailable",
            "stock_derivatives_score_status": "not_applicable" if fno_state == "not_eligible" else "unavailable",
            "cash_market_score_reason": "rolling delivery, volume and price baselines are not yet established",
            "stock_derivatives_score_reason": (
                "verified stock is not F&O eligible" if fno_state == "not_eligible"
                else "rolling stock-level OI history is not yet established"
            ),
        }
        output_rows.append(row)
        counts["universe"] += 1
        counts[f"cash_{cash_status}"] += 1
        counts[f"delivery_{delivery_status}"] += 1
        counts[f"futures_{futures_status}"] += 1
        counts[f"options_{options_status}"] += 1
        counts[f"deals_{deal_status}"] += 1

    output_rows.sort(key=lambda item: item["symbol"])
    rendered = _csv_bytes(output_rows)
    output_hash = _sha256(rendered)
    date_label = trade_date or "unknown-date"
    snapshot_dir = root / "data/snapshots/daily_stock_features"
    raw_dir = root / "data/raw/daily_stock_features"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"daily_stock_features_{date_label}_{output_hash[:16]}.csv"
    current_path = raw_dir / "daily_stock_features_current.csv"
    current_manifest_path = raw_dir / "daily_stock_features_current_manifest.json"
    if snapshot_path.exists() and _sha256(snapshot_path.read_bytes()) != output_hash:
        raise RuntimeError("immutable daily stock feature snapshot path collision")
    if not snapshot_path.exists():
        snapshot_path.write_bytes(rendered)
    current_path.write_bytes(rendered)
    manifest = {
        "dataset": "nifty500_daily_stock_evidence_matrix",
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "universe_count": len(universe),
        "universe_collection_status": (
            collector_status.get("nifty500_universe", {}).get("status", "not_run")
            if collector_status is not None else "not_run"
        ),
        "output_row_count": len(output_rows),
        "output_sha256": output_hash,
        "snapshot_path": str(snapshot_path.relative_to(root)),
        "current_path": str(current_path.relative_to(root)),
        "collector_run_status": collector_status or {},
        "sources": {
            "nifty500_universe": {
                "path": "data/raw/universe/nifty500_current.csv",
                "sha256": universe_hash,
                "row_count": len(universe),
            },
            "fno_status": fno_meta,
            "cash_market": cash_meta,
            "fno_bhavcopy": fo_meta,
            "bulk_block_deals": deals_meta,
        },
        "counts": dict(counts),
        "scoring_status": "input_matrix_only_no_production_scores",
        "limitations": [
            "Quarterly FPI ownership history is not yet joined.",
            "Bulk/block deal counterparties are unclassified and are not attributed to FPI.",
            "Cash-market and stock-derivatives family scores remain unavailable until rolling baselines are established.",
            "Stock futures/options activity is market-wide contract activity, not proof of FII positions.",
            "Market-wide FII/DII flows and participant aggregates are separate context, not stock-specific evidence.",
        ],
    }
    current_manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def _csv_bytes(rows: list[dict]) -> bytes:
    import io
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=FEATURE_COLUMNS, extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    manifest = build_daily_features(args.repo_root)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
