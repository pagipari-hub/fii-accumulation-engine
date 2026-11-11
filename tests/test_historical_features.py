import csv
import hashlib
from pathlib import Path

import pytest

from fii_engine.nse_fo_aggregate_reports import PARTICIPANT_COLUMNS
from fii_engine.historical_features import (
    _participant_window,
    build_historical_features,
)


def _write_snapshot(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _daily_row(day: str, symbol: str, close: int, *, delivery="40", buildup="long_buildup"):
    return {
        "trade_date": day,
        "symbol": symbol,
        "isin": f"INE{symbol}01001",
        "cash_data_status": "observed",
        "cash_close": str(close),
        "cash_volume": "1000",
        "delivery_status": "reported" if delivery else "not_reported",
        "delivery_percentage": delivery or "",
        "futures_data_status": "observed",
        "futures_buildup": buildup,
        "futures_expiry": "2026-11-26",
        "options_data_status": "observed",
        "options_expiry": "2026-11-26",
        "options_pcr_oi": "1.2",
    }


def _fii_rows(day: str):
    base = {
        "trade_date": day,
        "scope": "NSE_only",
        "segment": "capital_market",
        "publication_status": "provisional",
        "buy_value_crore": "100",
        "sell_value_crore": "80",
        "net_value_crore": "20",
        "gross_turnover_crore": "180",
        "net_flow_ratio": "0.111111",
    }
    return [
        {**base, "category": "FII/FPI"},
        {**base, "category": "DII", "publication_status": "exchange_reported"},
    ]


def _build_history(root: Path, *, missing_delivery_on: str | None = None):
    days = [f"2026-11-0{day}" for day in range(2, 7)]
    for index, day in enumerate(days):
        rows = [
            _daily_row(day, "ABC", 100 + index * 5,
                       delivery="" if day == missing_delivery_on else "40"),
            _daily_row(day, "XYZ", 200 - index * 2, buildup="short_covering"),
        ]
        path = root / "data/snapshots/daily_stock_features" / f"daily_stock_features_{day}_"
        # The content hash is part of the immutable snapshot name.
        import io
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        payload = buffer.getvalue().encode()
        digest = hashlib.sha256(payload).hexdigest()
        path = path.with_name(path.name + digest[:16] + ".csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

        fii = _fii_rows(day)
        fii_path = root / "data/snapshots/fii_dii" / f"nse_fii_dii_{day}_"
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(fii[0]))
        writer.writeheader()
        writer.writerows(fii)
        payload = buffer.getvalue().encode()
        digest = hashlib.sha256(payload).hexdigest()
        fii_path = fii_path.with_name(fii_path.name + digest[:16] + "_p2.csv")
        fii_path.parent.mkdir(parents=True, exist_ok=True)
        fii_path.write_bytes(payload)


def test_builds_five_session_stock_and_market_history(tmp_path):
    _build_history(tmp_path)
    result = build_historical_features(tmp_path)

    assert result["daily_stock_session_count"] == 5
    assert result["market_flow_session_count"] == 5
    assert result["as_of_trade_date"] == "2026-11-06"

    stock_path = tmp_path / "data/raw/historical_features/stock_historical_features_current.csv"
    with stock_path.open(newline="", encoding="utf-8") as handle:
        rows = {row["symbol"]: row for row in csv.DictReader(handle)}
    assert rows["ABC"]["5s_status"] == "complete"
    assert rows["ABC"]["5s_cash_return_pct"] == "20.0000"
    assert rows["ABC"]["5s_average_delivery_percentage"] == "40.0000"
    assert rows["ABC"]["5s_futures_buildup_status"] == "complete"
    assert rows["ABC"]["5s_options_pcr_status"] == "complete_same_expiry"
    assert rows["ABC"]["20s_status"] == "insufficient_history"

    market_path = tmp_path / "data/raw/historical_features/market_flow_history_current.csv"
    with market_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fii = next(row for row in rows if row["category"] == "FII/FPI" and row["window_sessions"] == "5")
    assert fii["status"] == "complete"
    assert fii["gross_buy_crore"] == "500.00"
    assert fii["gross_sell_crore"] == "400.00"
    assert fii["net_flow_crore"] == "100.00"
    assert fii["gross_turnover_crore"] == "900.00"
    assert fii["net_flow_ratio"] == "0.111111"


def test_delivery_history_is_partial_when_one_session_is_unreported(tmp_path):
    _build_history(tmp_path, missing_delivery_on="2026-11-04")
    build_historical_features(tmp_path)
    path = tmp_path / "data/raw/historical_features/stock_historical_features_current.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        abc = next(row for row in csv.DictReader(handle) if row["symbol"] == "ABC")
    assert abc["5s_status"] == "complete"
    assert abc["5s_delivery_status"] == "partial_observations"
    assert abc["5s_average_delivery_percentage"] == ""


def test_rejects_multiple_daily_snapshots_for_same_trade_date(tmp_path):
    _build_history(tmp_path)
    original = next((tmp_path / "data/snapshots/daily_stock_features").glob("*.csv"))
    with original.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["cash_close"] = "999"
    temporary = original.with_name("temporary.csv")
    digest = _write_snapshot(temporary, rows)
    duplicate = original.with_name(
        original.name.rsplit("_", 1)[0] + f"_{digest[:16]}.csv"
    )
    temporary.rename(duplicate)
    with pytest.raises(ValueError, match="multiple immutable stock snapshots"):
        build_historical_features(tmp_path)


def test_rejects_fii_snapshot_hash_mismatch(tmp_path):
    _build_history(tmp_path)
    path = next((tmp_path / "data/snapshots/fii_dii").glob("*_p2.csv"))
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="FII/DII snapshot filename hash mismatch"):
        build_historical_features(tmp_path)


def test_participant_oi_and_volume_history_remain_category_aggregates():
    oi_snapshots = []
    volume_snapshots = []
    key = ("FII", "stock_futures", "", "long")
    for index in range(5):
        day = f"2026-11-0{index + 2}"
        oi_snapshots.append({
            "date": day,
            "rows": {key: {"contracts": str(100 + index * 10)}},
        })
        volume_snapshots.append({
            "date": day,
            "rows": {key: {"contracts": "100"}},
        })

    oi = _participant_window(oi_snapshots, 5, "participant_oi")[0]
    volume = _participant_window(volume_snapshots, 5, "participant_volume")[0]

    assert oi["status"] == "complete"
    assert oi["start_contracts"] == "100"
    assert oi["end_contracts"] == "140"
    assert oi["change_contracts"] == "40"
    assert oi["change_pct"] == "40.0000"
    assert volume["status"] == "complete"
    assert volume["total_traded_contracts"] == "500"
    assert volume["average_daily_contracts"] == "100.00"
    assert oi["scope"] == "participant_category_aggregate_not_stock_specific"


def test_participant_history_reports_insufficient_sessions_and_partial_series():
    key = ("DII", "index_options", "put", "short")
    short_history = [{"date": "2026-11-06", "rows": {key: {"contracts": "10"}}}]
    insufficient = _participant_window(short_history, 5, "participant_oi")[0]
    assert insufficient["status"] == "insufficient_history"

    complete_dates = [
        {"date": f"2026-11-0{day}", "rows": {key: {"contracts": "10"}}}
        for day in range(2, 7)
    ]
    complete_dates[2]["rows"] = {}
    partial = _participant_window(complete_dates, 5, "participant_oi")[0]
    assert partial["status"] == "partial_observations"


def _write_archive_participant_csv(path: Path, dataset: str, day: str, value: int) -> str:
    from datetime import date
    import io

    title = "Open Interest" if dataset == "participant_oi" else "Trading Volume"
    parsed_day = date.fromisoformat(day)
    lines = [f'"{title} as on {parsed_day.strftime("%b")} {parsed_day.day}, {parsed_day.year}"']
    rows = []
    numeric_columns = PARTICIPANT_COLUMNS[1:]
    participant_values = {}
    for category in ("Client", "DII", "FII", "Pro"):
        values = {column: 0 for column in numeric_columns}
        if category == "FII":
            values["Future Stock Long"] = value
        values["Total Long Contracts"] = sum(
            amount for column, amount in values.items() if column.endswith(" Long")
        )
        values["Total Short Contracts"] = sum(
            amount for column, amount in values.items() if column.endswith(" Short")
        )
        participant_values[category] = values
    total = {
        column: sum(participant_values[category][column] for category in participant_values)
        for column in numeric_columns
    }
    rows.extend([[category] + [participant_values[category][column] for column in numeric_columns]
                 for category in ("Client", "DII", "FII", "Pro")])
    rows.append(["TOTAL"] + [total[column] for column in numeric_columns])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(PARTICIPANT_COLUMNS)
    writer.writerows(rows)
    payload = ("\n".join(lines) + "\n" + buffer.getvalue()).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _write_archive_manifest(root: Path, days: list[str]) -> None:
    import json

    records = []
    for index, day in enumerate(days):
        for dataset, value in (
            ("participant_oi", 100 + index * 10),
            ("participant_volume", 200 + index * 10),
        ):
            digest = _write_archive_participant_csv(
                root / "data/raw/nse_fo_historical" / day / f"{dataset}_{index}.csv",
                dataset, day, value,
            )
            path = root / "data/raw/nse_fo_historical" / day / f"{dataset}_{index}.csv"
            records.append({
                "dataset": dataset,
                "trade_date": day,
                "payload_path": str(path.relative_to(root)),
                "payload_sha256": digest,
                "inspection": {"status": "validated"},
            })
        stats_payload = f"validated aggregate FII statistics {day}".encode()
        stats_path = root / "data/raw/nse_fo_historical" / day / f"fii_stats_{index}.xls"
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_bytes(stats_payload)
        records.append({
            "dataset": "fii_derivatives_statistics",
            "trade_date": day,
            "payload_path": str(stats_path.relative_to(root)),
            "payload_sha256": hashlib.sha256(stats_payload).hexdigest(),
            "inspection": {"status": "validated"},
        })
    manifest_path = root / "data/raw/nse_fo_historical/historical_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "coverage_status": "target_met",
        "requested_complete_sessions": len(days),
        "complete_sessions": len(days),
        "records": records,
    }), encoding="utf-8")


def test_historical_archive_participant_reports_feed_rolling_features(tmp_path):
    _build_history(tmp_path)
    days = [f"2026-11-0{day}" for day in range(2, 7)]
    _write_archive_manifest(tmp_path, days)

    result = build_historical_features(tmp_path)

    assert result["participant_archive_complete_session_count"] == 5
    assert result["participant_archive_complete_trade_dates"] == days
    assert result["participant_oi_session_count"] == 5
    assert result["participant_volume_session_count"] == 5

    path = tmp_path / "data/raw/historical_features/participant_derivatives_history_current.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    oi = next(row for row in rows
              if row["dataset"] == "participant_oi"
              and row["participant_category"] == "FII"
              and row["instrument_group"] == "stock_futures"
              and row["source_side"] == "long"
              and row["window_sessions"] == "5")
    volume = next(row for row in rows
                  if row["dataset"] == "participant_volume"
                  and row["participant_category"] == "FII"
                  and row["instrument_group"] == "stock_futures"
                  and row["source_side"] == "long"
                  and row["window_sessions"] == "5")
    assert oi["status"] == "complete"
    assert oi["start_contracts"] == "100"
    assert oi["end_contracts"] == "140"
    assert oi["change_contracts"] == "40"
    assert volume["status"] == "complete"
    assert volume["total_traded_contracts"] == "1100"
    assert oi["scope"] == "participant_category_aggregate_not_stock_specific"


def test_historical_archive_rejects_payload_hash_mismatch(tmp_path):
    _build_history(tmp_path)
    days = ["2026-11-02"]
    _write_archive_manifest(tmp_path, days)
    path = next((tmp_path / "data/raw/nse_fo_historical/2026-11-02").glob("participant_oi_*.csv"))
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="archive payload hash mismatch"):
        build_historical_features(tmp_path)


def test_historical_archive_requires_all_three_report_families(tmp_path):
    _build_history(tmp_path)
    days = ["2026-11-02"]
    _write_archive_manifest(tmp_path, days)
    manifest_path = tmp_path / "data/raw/nse_fo_historical/historical_manifest.json"
    import json
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records"] = [
        record for record in manifest["records"]
        if record["dataset"] != "fii_derivatives_statistics"
    ]
    manifest["complete_sessions"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = build_historical_features(tmp_path)
    assert result["participant_archive_complete_session_count"] == 0
    assert result["participant_oi_session_count"] == 0
    assert result["participant_volume_session_count"] == 0
