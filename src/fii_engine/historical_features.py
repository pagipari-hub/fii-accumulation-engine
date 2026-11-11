"""Build validated rolling stock and market-wide institutional history.

This module deliberately reports coverage before producing any rolling metric.
It is not an FPI attribution or ranking module.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fii_engine.nse_fo_aggregate_reports import inspect_csv, normalize_participant_report

WINDOWS = (5, 20, 60)
DAILY_REQUIRED = {
    "trade_date", "symbol", "isin", "cash_data_status", "cash_close",
    "cash_volume", "delivery_status", "delivery_percentage",
    "futures_data_status", "futures_buildup", "futures_expiry",
    "options_data_status", "options_pcr_oi",
}
FII_REQUIRED = {
    "trade_date", "category", "buy_value_crore", "sell_value_crore",
    "net_value_crore", "gross_turnover_crore", "net_flow_ratio",
    "publication_status", "scope", "segment",
}
FII_NAME = re.compile(
    r"^nse_fii_dii_(\d{4}-\d{2}-\d{2})_([0-9a-f]{16})_p(\d+)\.csv$"
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _decimal(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid historical numeric value: {text!r}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite historical numeric value: {text!r}")
    return number


def _read_csv(path: Path) -> tuple[list[dict], str]:
    payload = path.read_bytes()
    digest = _sha256(payload)
    try:
        reader = csv.DictReader(payload.decode("utf-8-sig").splitlines())
    except UnicodeDecodeError as exc:
        raise ValueError(f"historical CSV is not UTF-8: {path}") from exc
    if not reader.fieldnames:
        raise ValueError(f"historical CSV has no header: {path}")
    rows = list(reader)
    return rows, digest



_DAILY_STATUS_RANKS = {
    "cash_data_status": {"observed": 2, "not_applicable": 1, "unavailable": 0, "unknown": 0},
    "delivery_status": {"reported": 3, "not_reported": 2, "not_applicable": 1, "not_found": 0, "unavailable": 0, "unknown": 0},
    "futures_data_status": {"observed": 2, "not_applicable": 1, "unavailable": 0, "unknown": 0},
    "options_data_status": {"observed": 2, "not_applicable": 1, "unavailable": 0, "unknown": 0},
    "fno_status": {"eligible": 2, "not_eligible": 2, "unknown": 0},
}
_DAILY_MEASUREMENT_FIELDS = (
    "cash_close", "cash_previous_close", "cash_return_pct", "cash_volume",
    "cash_turnover", "cash_trade_count", "delivery_quantity",
    "delivery_percentage", "futures_expiry", "futures_close",
    "futures_previous_close", "futures_return_pct", "futures_open_interest",
    "futures_oi_change", "futures_oi_change_pct", "futures_volume",
    "futures_buildup", "options_expiry", "call_open_interest",
    "put_open_interest", "options_pcr_oi", "call_volume", "put_volume",
    "options_pcr_volume",
)


def _daily_snapshot_dominates(candidate: dict, other: dict) -> bool:
    """Return true only when candidate is strictly more complete without conflicts."""
    if candidate["symbols"] != other["symbols"] or candidate["isins"] != other["isins"]:
        raise ValueError(
            f"daily snapshots for {candidate['date']} have different symbol/ISIN identity sets"
        )
    strictly_better = False
    for symbol in candidate["symbols"]:
        left = candidate["rows"][symbol]
        right = other["rows"][symbol]
        for field in _DAILY_MEASUREMENT_FIELDS:
            left_value = str(left.get(field, "") or "").strip()
            right_value = str(right.get(field, "") or "").strip()
            if left_value and right_value and left_value != right_value:
                raise ValueError(
                    f"conflicting daily snapshot values for {candidate['date']} "
                    f"{symbol}/{field}; manual revision review required"
                )
            if left_value and not right_value:
                strictly_better = True
            elif right_value and not left_value:
                return False
        for field, ranks in _DAILY_STATUS_RANKS.items():
            left_status = str(left.get(field, "unknown") or "unknown").strip()
            right_status = str(right.get(field, "unknown") or "unknown").strip()
            left_rank = ranks.get(left_status, 0)
            right_rank = ranks.get(right_status, 0)
            if left_rank < right_rank:
                return False
            if left_rank > right_rank:
                strictly_better = True
    return strictly_better


def _load_daily_snapshots(root: Path) -> tuple[list[dict], list[str]]:
    directory = root / "data/snapshots/daily_stock_features"
    paths = sorted(directory.glob("daily_stock_features_*.csv"))
    if not paths:
        raise FileNotFoundError("no immutable daily stock feature snapshots found")
    by_date: dict[str, dict] = {}
    for path in paths:
        match = re.fullmatch(
            r"daily_stock_features_(\d{4}-\d{2}-\d{2})_([0-9a-f]{16})\.csv",
            path.name,
        )
        if not match:
            raise ValueError(f"unexpected daily snapshot filename: {path.name}")
        snapshot_date, expected_prefix = match.groups()
        date.fromisoformat(snapshot_date)
        rows, digest = _read_csv(path)
        if digest[:16] != expected_prefix:
            raise ValueError(f"daily snapshot filename hash mismatch: {path}")
        if not rows:
            raise ValueError(f"empty daily stock snapshot: {path}")
        if not DAILY_REQUIRED.issubset(rows[0]):
            raise ValueError(
                f"daily stock snapshot schema drift: {path}; "
                f"missing={sorted(DAILY_REQUIRED - set(rows[0]))}"
            )
        symbols = [row["symbol"] for row in rows]
        if any(not symbol for symbol in symbols) or len(symbols) != len(set(symbols)):
            raise ValueError(f"blank or duplicate stock symbols in {path}")
        if any(row["trade_date"] != snapshot_date for row in rows):
            raise ValueError(f"row trade date does not match snapshot filename: {path}")
        isins = {row["symbol"]: row["isin"] for row in rows}
        if any(not value for value in isins.values()):
            raise ValueError(f"blank ISIN in daily stock snapshot: {path}")
        candidate = {
            "date": snapshot_date,
            "rows": {row["symbol"]: row for row in rows},
            "symbols": set(symbols),
            "isins": isins,
            "path": str(path.relative_to(root)),
            "sha256": digest,
            "row_count": len(rows),
        }
        previous = by_date.get(snapshot_date)
        if previous is not None:
            if _daily_snapshot_dominates(candidate, previous):
                candidate["superseded_snapshot_paths"] = (
                    previous.get("superseded_snapshot_paths", []) + [previous["path"]]
                )
                by_date[snapshot_date] = candidate
            elif _daily_snapshot_dominates(previous, candidate):
                previous.setdefault("superseded_snapshot_paths", []).append(candidate["path"])
            else:
                raise ValueError(
                    f"multiple immutable stock snapshots exist for {snapshot_date} "
                    "without a strict completeness-dominance resolution"
                )
        else:
            by_date[snapshot_date] = candidate
    dates = sorted(by_date)
    return [by_date[item] for item in dates], dates


def _load_fii_snapshots(root: Path) -> tuple[list[dict], list[str]]:
    directory = root / "data/snapshots/fii_dii"
    candidates: dict[str, list[dict]] = defaultdict(list)
    for path in directory.glob("nse_fii_dii_*.csv"):
        match = FII_NAME.fullmatch(path.name)
        if not match:
            continue
        snapshot_date, expected_prefix, parser_version = match.groups()
        date.fromisoformat(snapshot_date)
        rows, digest = _read_csv(path)
        if digest[:16] != expected_prefix:
            raise ValueError(f"FII/DII snapshot filename hash mismatch: {path}")
        if not rows or not FII_REQUIRED.issubset(rows[0]):
            continue  # Older parser versions do not contain the complete gross-flow schema.
        if any(row["trade_date"] != snapshot_date for row in rows):
            raise ValueError(f"FII/DII row date does not match filename: {path}")
        if any(row["scope"] != "NSE_only" or row["segment"] != "capital_market" for row in rows):
            raise ValueError(f"unexpected FII/DII scope or segment in {path}")
        categories = [row["category"] for row in rows]
        if set(categories) != {"FII/FPI", "DII"} or len(categories) != 2:
            raise ValueError(f"incomplete or duplicate FII/DII categories in {path}")
        candidates[snapshot_date].append({
            "date": snapshot_date,
            "rows": {row["category"]: row for row in rows},
            "parser_version": int(parser_version),
            "path": str(path.relative_to(root)),
            "sha256": digest,
        })

    selected = []
    for snapshot_date in sorted(candidates):
        versions = candidates[snapshot_date]
        highest = max(item["parser_version"] for item in versions)
        latest = [item for item in versions if item["parser_version"] == highest]
        hashes = {item["sha256"] for item in latest}
        if len(hashes) != 1:
            raise ValueError(
                f"multiple FII/DII snapshots at parser version {highest} for "
                f"{snapshot_date}; resolve revised values before rolling analysis"
            )
        selected.append(latest[0])
    return selected, [item["date"] for item in selected]


def _window_stock(
    stock: dict,
    session_rows: list[dict],
    expected_sessions: int,
) -> dict:
    output = {"status": "insufficient_history", "sessions_available": len(session_rows)}
    if len(session_rows) < expected_sessions:
        return output

    symbol = stock["symbol"]
    observations = [item["rows"].get(symbol) for item in session_rows]
    if any(row is None for row in observations):
        output["status"] = "partial_universe_history"
        output["stock_observations"] = sum(row is not None for row in observations)
        return output

    # The same ISIN must identify the symbol throughout the window. Symbol changes
    # and corporate actions are not silently stitched together.
    isins = {row["isin"] for row in observations}
    if len(isins) != 1 or next(iter(isins)) != stock["isin"]:
        output["status"] = "identity_changed"
        return output

    output["status"] = "complete"
    output["stock_observations"] = len(observations)

    cash = [
        _decimal(row["cash_close"])
        if row["cash_data_status"] == "observed" else None
        for row in observations
    ]
    if all(value is not None and value > 0 for value in cash):
        output["cash_return_pct"] = format(
            (cash[-1] / cash[0] - Decimal(1)) * Decimal(100), ".4f"
        )
        output["cash_return_status"] = "complete"
    else:
        output["cash_return_pct"] = None
        output["cash_return_status"] = "partial_observations"

    volumes = [
        _decimal(row["cash_volume"])
        if row["cash_data_status"] == "observed" else None
        for row in observations
    ]
    if all(value is not None for value in volumes):
        output["average_daily_volume"] = format(
            sum(volumes, Decimal(0)) / Decimal(expected_sessions), ".2f"
        )
        output["volume_status"] = "complete"
    else:
        output["average_daily_volume"] = None
        output["volume_status"] = "partial_observations"

    delivery = [
        _decimal(row["delivery_percentage"])
        if row["delivery_status"] == "reported" else None
        for row in observations
    ]
    if all(value is not None for value in delivery):
        output["average_delivery_percentage"] = format(
            sum(delivery, Decimal(0)) / Decimal(expected_sessions), ".4f"
        )
        output["delivery_status"] = "complete"
    else:
        output["average_delivery_percentage"] = None
        output["delivery_status"] = "partial_observations"

    buildup = Counter(
        row["futures_buildup"]
        for row in observations
        if row["futures_data_status"] == "observed" and row["futures_buildup"]
    )
    output["futures_observations"] = sum(buildup.values())
    output["futures_buildup_counts"] = json.dumps(dict(sorted(buildup.items())), sort_keys=True)
    output["futures_buildup_status"] = (
        "complete" if sum(buildup.values()) == expected_sessions
        else "partial_observations"
    )

    expiries = [row["options_expiry"] for row in observations if row["options_data_status"] == "observed"]
    pcr_values = [
        _decimal(row["options_pcr_oi"])
        for row in observations
        if row["options_data_status"] == "observed"
    ]
    if (
        len(expiries) == expected_sessions
        and len(set(expiries)) == 1
        and all(value is not None for value in pcr_values)
    ):
        output["average_options_pcr_oi_same_expiry"] = format(
            sum(pcr_values, Decimal(0)) / Decimal(expected_sessions), ".4f"
        )
        output["options_pcr_status"] = "complete_same_expiry"
    else:
        output["average_options_pcr_oi_same_expiry"] = None
        output["options_pcr_status"] = (
            "contract_roll_or_partial_data" if expiries else "partial_observations"
        )
    return output


def _window_market(
    snapshot_rows: list[dict],
    expected_sessions: int,
) -> dict:
    if len(snapshot_rows) < expected_sessions:
        return {"status": "insufficient_history", "sessions_available": len(snapshot_rows)}
    categories = {}
    for category in ("FII/FPI", "DII"):
        rows = [item["rows"][category] for item in snapshot_rows]
        if len(rows) != expected_sessions:
            return {"status": "partial_observations", "sessions_available": len(rows)}
        fields = {}
        for source, target in (
            ("buy_value_crore", "gross_buy_crore"),
            ("sell_value_crore", "gross_sell_crore"),
            ("net_value_crore", "net_flow_crore"),
            ("gross_turnover_crore", "gross_turnover_crore"),
        ):
            values = [_decimal(row[source]) for row in rows]
            if any(value is None for value in values):
                fields[target] = None
            else:
                fields[target] = format(sum(values, Decimal(0)), ".2f")
        gross_values = [_decimal(row["gross_turnover_crore"]) for row in rows]
        net_values = [_decimal(row["net_value_crore"]) for row in rows]
        if any(value is None for value in gross_values + net_values):
            return {"status": "partial_observations", "sessions_available": len(rows)}
        total_gross = sum(gross_values, Decimal(0))
        total_net = sum(net_values, Decimal(0))
        fields["net_flow_ratio"] = (
            format(total_net / total_gross, ".6f") if total_gross != 0 else None
        )
        fields["publication_statuses"] = ";".join(sorted({row["publication_status"] for row in rows}))
        categories[category] = fields
    return {"status": "complete", "sessions_available": expected_sessions, "categories": categories}


PARTICIPANT_REQUIRED = {
    "trade_date", "dataset", "metric", "unit", "participant_category",
    "instrument_group", "option_type", "source_side", "contracts",
}
PARTICIPANT_FILENAME = re.compile(
    r"^(participant_oi|participant_volume)_(\d{4}-\d{2}-\d{2})_([0-9a-f]{16})_p(\d+)\.csv$"
)


def _load_participant_snapshots(root: Path) -> dict[str, list[dict]]:
    directory = root / "data/snapshots/fno_aggregate_reports"
    candidates: dict[str, dict[str, list[dict]]] = {
        "participant_oi": defaultdict(list),
        "participant_volume": defaultdict(list),
    }
    for path in directory.glob("participant_*.csv"):
        match = PARTICIPANT_FILENAME.fullmatch(path.name)
        if not match:
            continue
        dataset, snapshot_date, expected_prefix, parser_version = match.groups()
        date.fromisoformat(snapshot_date)
        rows, digest = _read_csv(path)
        if digest[:16] != expected_prefix:
            raise ValueError(f"participant snapshot filename hash mismatch: {path}")
        if not rows or not PARTICIPANT_REQUIRED.issubset(rows[0]):
            raise ValueError(f"participant snapshot schema drift: {path}")
        keys = set()
        for row in rows:
            if row["trade_date"] != snapshot_date or row["dataset"] != dataset:
                raise ValueError(f"participant snapshot date/dataset mismatch: {path}")
            if row["unit"] != "contracts":
                raise ValueError(f"unexpected participant derivative unit in {path}")
            key = (
                row["participant_category"], row["instrument_group"],
                row["option_type"], row["source_side"],
            )
            if key in keys:
                raise ValueError(f"duplicate participant derivative series in {path}: {key}")
            keys.add(key)
        candidates[dataset][snapshot_date].append({
            "date": snapshot_date,
            "rows": {
                (
                    row["participant_category"], row["instrument_group"],
                    row["option_type"], row["source_side"],
                ): row
                for row in rows
            },
            "parser_version": int(parser_version),
            "path": str(path.relative_to(root)),
            "sha256": digest,
            "row_count": len(rows),
        })

    result = {}
    for dataset, by_date in candidates.items():
        selected = []
        for snapshot_date in sorted(by_date):
            versions = by_date[snapshot_date]
            highest = max(item["parser_version"] for item in versions)
            latest = [item for item in versions if item["parser_version"] == highest]
            if len({item["sha256"] for item in latest}) != 1:
                raise ValueError(
                    f"multiple {dataset} snapshots at parser version {highest} "
                    f"for {snapshot_date}; resolve revision before rolling analysis"
                )
            selected.append(latest[0])
        result[dataset] = selected
    return result



def _load_historical_archive_participant_snapshots(root: Path) -> tuple[dict[str, list[dict]], list[str]]:
    """Load participant reports only from complete, hash-verified NSE archive dates.

    A date is eligible only when the archive manifest records all three validated
    report families (participant OI, participant volume and FII derivatives stats).
    The latter is a completeness gate, not stock-level attribution evidence.
    """
    manifest_path = root / "data/raw/nse_fo_historical/historical_manifest.json"
    if not manifest_path.is_file():
        return {"participant_oi": [], "participant_volume": []}, []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("historical NSE F&O archive manifest has no records list")

    by_date: dict[str, dict[str, dict]] = defaultdict(dict)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("historical NSE F&O archive manifest contains an invalid record")
        dataset = record.get("dataset")
        trade_date = record.get("trade_date")
        if dataset not in {"participant_oi", "participant_volume", "fii_derivatives_statistics"}:
            continue
        date.fromisoformat(trade_date)
        if dataset in by_date[trade_date]:
            raise ValueError(f"duplicate {dataset} archive record for {trade_date}")
        by_date[trade_date][dataset] = record

    required_datasets = {
        "participant_oi", "participant_volume", "fii_derivatives_statistics"
    }
    complete_dates = sorted(
        trade_date for trade_date, items in by_date.items()
        if set(items) == required_datasets
    )
    declared_complete = manifest.get("complete_sessions")
    if declared_complete is not None and declared_complete != len(complete_dates):
        raise ValueError(
            "historical NSE F&O manifest complete-session count does not match "
            f"its validated record families: {declared_complete} != {len(complete_dates)}"
        )
    result: dict[str, list[dict]] = {
        "participant_oi": [], "participant_volume": []
    }

    for trade_date in complete_dates:
        validated_payloads: dict[str, bytes] = {}
        for dataset in sorted(required_datasets):
            record = by_date[trade_date][dataset]
            payload_path_value = record.get("payload_path")
            expected_hash = record.get("payload_sha256")
            if not isinstance(payload_path_value, str) or not payload_path_value:
                raise ValueError(f"{dataset} archive record has no payload path for {trade_date}")
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                raise ValueError(f"{dataset} archive record has no valid payload SHA-256 for {trade_date}")
            payload_path = Path(payload_path_value)
            if payload_path.is_absolute() or ".." in payload_path.parts:
                raise ValueError(f"unsafe archive payload path for {dataset} on {trade_date}")
            resolved_path = (root / payload_path).resolve()
            if root.resolve() not in resolved_path.parents:
                raise ValueError(f"archive payload escapes repository root: {payload_path_value}")
            if not resolved_path.is_file():
                raise FileNotFoundError(f"missing archived {dataset} payload: {payload_path_value}")
            payload = resolved_path.read_bytes()
            if _sha256(payload) != expected_hash:
                raise ValueError(f"archive payload hash mismatch: {payload_path_value}")
            if not isinstance(record.get("inspection"), dict):
                raise ValueError(f"archive payload lacks successful inspection metadata: {dataset} {trade_date}")
            validated_payloads[dataset] = payload

        for dataset in ("participant_oi", "participant_volume"):
            inspection = inspect_csv(
                validated_payloads[dataset], dataset, expected_trade_date=trade_date
            )
            normalized = normalize_participant_report(inspection, dataset, trade_date)
            expected_count = 4 * 12
            if len(normalized) != expected_count:
                raise ValueError(
                    f"{dataset} normalized row count changed for {trade_date}: "
                    f"{len(normalized)} != {expected_count}"
                )
            rows = {}
            for row in normalized:
                key = (
                    row["participant_category"], row["instrument_group"],
                    row["option_type"], row["source_side"],
                )
                if key in rows:
                    raise ValueError(f"duplicate normalized participant series for {dataset} {trade_date}: {key}")
                rows[key] = row
            result[dataset].append({
                "date": trade_date,
                "rows": rows,
                "parser_version": 3,
                "path": by_date[trade_date][dataset]["payload_path"],
                "sha256": by_date[trade_date][dataset]["payload_sha256"],
                "row_count": len(normalized),
                "source": "official_nse_historical_archive",
            })

    return result, complete_dates


def _load_all_participant_snapshots(root: Path) -> tuple[dict[str, list[dict]], list[str]]:
    existing = _load_participant_snapshots(root)
    archived, archive_dates = _load_historical_archive_participant_snapshots(root)
    merged: dict[str, list[dict]] = {}
    for dataset in ("participant_oi", "participant_volume"):
        by_date = {item["date"]: item for item in existing[dataset]}
        for item in archived[dataset]:
            previous = by_date.get(item["date"])
            if previous is not None:
                if previous["rows"] != item["rows"]:
                    raise ValueError(
                        f"normalized snapshot and official archive disagree for "
                        f"{dataset} on {item['date']}"
                    )
                continue
            by_date[item["date"]] = item
        merged[dataset] = [by_date[key] for key in sorted(by_date)]
    return merged, archive_dates


def _participant_window(
    snapshots: list[dict],
    expected_sessions: int,
    dataset: str,
) -> list[dict]:
    if not snapshots:
        return []
    latest_keys = sorted(snapshots[-1]["rows"])
    selected = snapshots[-expected_sessions:]
    output = []
    for key in latest_keys:
        category, instrument_group, option_type, source_side = key
        base = {
            "dataset": dataset,
            "participant_category": category,
            "instrument_group": instrument_group,
            "option_type": option_type,
            "source_side": source_side,
            "window_sessions": expected_sessions,
            "sessions_available": len(selected),
            "scope": "participant_category_aggregate_not_stock_specific",
        }
        if len(selected) < expected_sessions:
            output.append({**base, "status": "insufficient_history"})
            continue
        series = [snapshot["rows"].get(key) for snapshot in selected]
        if any(row is None for row in series):
            output.append({**base, "status": "partial_observations"})
            continue
        values = [_decimal(row["contracts"]) for row in series]
        if any(value is None for value in values):
            output.append({**base, "status": "partial_observations"})
            continue
        if dataset == "participant_oi":
            start, end = values[0], values[-1]
            change = end - start
            output.append({
                **base,
                "status": "complete",
                "start_contracts": format(start, ".0f"),
                "end_contracts": format(end, ".0f"),
                "change_contracts": format(change, ".0f"),
                "change_pct": (
                    format(change / start * Decimal(100), ".4f")
                    if start != 0 else None
                ),
            })
        else:
            total = sum(values, Decimal(0))
            output.append({
                **base,
                "status": "complete",
                "total_traded_contracts": format(total, ".0f"),
                "average_daily_contracts": format(
                    total / Decimal(expected_sessions), ".2f"
                ),
            })
    return output


def build_historical_features(root: Path = Path(".")) -> dict:
    root = root.resolve()
    daily, daily_dates = _load_daily_snapshots(root)
    fii, fii_dates = _load_fii_snapshots(root)
    participant_history, participant_archive_dates = _load_all_participant_snapshots(root)
    latest = daily[-1]
    latest_rows = [latest["rows"][symbol] for symbol in sorted(latest["symbols"])]

    output_rows = []
    for stock in latest_rows:
        result = {
            "trade_date": latest["date"],
            "symbol": stock["symbol"],
            "isin": stock["isin"],
        }
        for window in WINDOWS:
            observations = daily[-window:]
            metrics = _window_stock(stock, observations, window)
            for key, value in metrics.items():
                result[f"{window}s_{key}"] = value
        output_rows.append(result)

    market_rows = []
    for window in WINDOWS:
        selected = fii[-window:]
        result = _window_market(selected, window)
        for category in ("FII/FPI", "DII"):
            category_values = result.get("categories", {}).get(category, {})
            market_rows.append({
                "as_of_trade_date": fii[-1]["date"] if fii else None,
                "category": category,
                "window_sessions": window,
                "status": result["status"],
                "sessions_available": result["sessions_available"],
                **category_values,
            })

    participant_rows = []
    for dataset in ("participant_oi", "participant_volume"):
        for window in WINDOWS:
            participant_rows.extend(
                _participant_window(participant_history[dataset], window, dataset)
            )

    stock_fields = list(output_rows[0]) if output_rows else []
    market_fields = list(market_rows[0]) if market_rows else [
        "as_of_trade_date", "category", "window_sessions", "status", "sessions_available",
        "gross_buy_crore", "gross_sell_crore", "net_flow_crore",
        "gross_turnover_crore", "net_flow_ratio", "publication_statuses",
    ]
    output_dir = root / "data/raw/historical_features"
    snapshot_dir = root / "data/snapshots/historical_features"
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    def write_csv(path: Path, rows: list[dict], fields: list[str]) -> str:
        import io
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        payload = buffer.getvalue().encode("utf-8")
        path.write_bytes(payload)
        return _sha256(payload)

    participant_fields = [
        "dataset", "participant_category", "instrument_group", "option_type",
        "source_side", "window_sessions", "sessions_available", "scope", "status",
        "start_contracts", "end_contracts", "change_contracts", "change_pct",
        "total_traded_contracts", "average_daily_contracts",
    ]
    stock_current = output_dir / "stock_historical_features_current.csv"
    market_current = output_dir / "market_flow_history_current.csv"
    participant_current = output_dir / "participant_derivatives_history_current.csv"
    stock_hash = write_csv(stock_current, output_rows, stock_fields)
    market_hash = write_csv(market_current, market_rows, market_fields)
    participant_hash = write_csv(participant_current, participant_rows, participant_fields)
    stock_snapshot = snapshot_dir / f"stock_historical_features_{latest['date']}_{stock_hash[:16]}.csv"
    market_snapshot = snapshot_dir / f"market_flow_history_{latest['date']}_{market_hash[:16]}.csv"
    participant_snapshot = snapshot_dir / f"participant_derivatives_history_{latest['date']}_{participant_hash[:16]}.csv"
    for current, snapshot in (
        (stock_current, stock_snapshot),
        (market_current, market_snapshot),
        (participant_current, participant_snapshot),
    ):
        payload = current.read_bytes()
        if snapshot.exists() and snapshot.read_bytes() != payload:
            raise RuntimeError(f"immutable historical snapshot collision: {snapshot}")
        if not snapshot.exists():
            snapshot.write_bytes(payload)

    manifest = {
        "dataset": "validated_historical_evidence_features",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_trade_date": latest["date"],
        "daily_stock_session_count": len(daily),
        "daily_stock_trade_dates": daily_dates,
        "daily_snapshot_revision_resolutions": [
            {
                "trade_date": item["date"],
                "selected_snapshot_path": item["path"],
                "superseded_snapshot_paths": item["superseded_snapshot_paths"],
                "rule": "strict_identity_and_measurement_consistent_completeness_dominance",
            }
            for item in daily if item.get("superseded_snapshot_paths")
        ],
        "market_flow_session_count": len(fii),
        "market_flow_trade_dates": fii_dates,
        "participant_oi_session_count": len(participant_history["participant_oi"]),
        "participant_oi_trade_dates": [item["date"] for item in participant_history["participant_oi"]],
        "participant_volume_session_count": len(participant_history["participant_volume"]),
        "participant_volume_trade_dates": [item["date"] for item in participant_history["participant_volume"]],
        "participant_archive_complete_session_count": len(participant_archive_dates),
        "participant_archive_complete_trade_dates": participant_archive_dates,
        "participant_archive_manifest_path": (
            "data/raw/nse_fo_historical/historical_manifest.json"
            if (root / "data/raw/nse_fo_historical/historical_manifest.json").is_file()
            else None
        ),
        "participant_archive_manifest_sha256": (
            _sha256((root / "data/raw/nse_fo_historical/historical_manifest.json").read_bytes())
            if (root / "data/raw/nse_fo_historical/historical_manifest.json").is_file()
            else None
        ),
        "windows_sessions": list(WINDOWS),
        "stock_universe_count": len(latest_rows),
        "stock_output_sha256": stock_hash,
        "stock_snapshot_path": str(stock_snapshot.relative_to(root)),
        "market_output_sha256": market_hash,
        "market_snapshot_path": str(market_snapshot.relative_to(root)),
        "participant_output_sha256": participant_hash,
        "participant_snapshot_path": str(participant_snapshot.relative_to(root)),
        "limitations": [
            "A window is complete only when the requested number of distinct committed trading-date snapshots exists.",
            "Stock cash, delivery and derivatives metrics retain family-specific partial-observation statuses.",
            "Options PCR rolling averages are withheld across expiry changes.",
            "Futures buildup counts describe market-wide contract activity, not FII attribution.",
            "Market-wide FII/FPI and DII aggregates are separate context and never stock-level attribution.",
            "FII/FPI publication status is preserved; rolling sums are not a final custodial series.",
            "Participant OI and volume are participant-category aggregates, never participant-by-stock positions.",
        ],
    }
    manifest_path = output_dir / "historical_features_current_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(build_historical_features(args.repo_root), indent=2))
    

if __name__ == "__main__":
    main()
