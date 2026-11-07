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
        if snapshot_date in by_date:
            raise ValueError(
                f"multiple immutable stock snapshots exist for {snapshot_date}; "
                "resolve the corrected/revised snapshot explicitly before rolling analysis"
            )
        by_date[snapshot_date] = {
            "date": snapshot_date,
            "rows": {row["symbol"]: row for row in rows},
            "symbols": set(symbols),
            "isins": isins,
            "path": str(path.relative_to(root)),
            "sha256": digest,
            "row_count": len(rows),
        }
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


def build_historical_features(root: Path = Path(".")) -> dict:
    root = root.resolve()
    daily, daily_dates = _load_daily_snapshots(root)
    fii, fii_dates = _load_fii_snapshots(root)
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

    stock_current = output_dir / "stock_historical_features_current.csv"
    market_current = output_dir / "market_flow_history_current.csv"
    stock_hash = write_csv(stock_current, output_rows, stock_fields)
    market_hash = write_csv(market_current, market_rows, market_fields)
    stock_snapshot = snapshot_dir / f"stock_historical_features_{latest['date']}_{stock_hash[:16]}.csv"
    market_snapshot = snapshot_dir / f"market_flow_history_{latest['date']}_{market_hash[:16]}.csv"
    for current, snapshot in ((stock_current, stock_snapshot), (market_current, market_snapshot)):
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
        "market_flow_session_count": len(fii),
        "market_flow_trade_dates": fii_dates,
        "windows_sessions": list(WINDOWS),
        "stock_universe_count": len(latest_rows),
        "stock_output_sha256": stock_hash,
        "stock_snapshot_path": str(stock_snapshot.relative_to(root)),
        "market_output_sha256": market_hash,
        "market_snapshot_path": str(market_snapshot.relative_to(root)),
        "limitations": [
            "A window is complete only when the requested number of distinct committed trading-date snapshots exists.",
            "Stock cash, delivery and derivatives metrics retain family-specific partial-observation statuses.",
            "Options PCR rolling averages are withheld across expiry changes.",
            "Futures buildup counts describe market-wide contract activity, not FII attribution.",
            "Market-wide FII/FPI and DII aggregates are separate context and never stock-level attribution.",
            "FII/FPI publication status is preserved; rolling sums are not a final custodial series.",
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
