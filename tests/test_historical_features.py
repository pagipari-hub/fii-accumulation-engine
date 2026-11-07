import csv
import hashlib
from pathlib import Path

import pytest

from fii_engine.historical_features import build_historical_features


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
