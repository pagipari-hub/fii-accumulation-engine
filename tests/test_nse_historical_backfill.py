import csv
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from fii_engine.daily_stock_features import FEATURE_COLUMNS
from fii_engine.nse_historical_backfill import run_backfill


def _zip_csv(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.csv", text)
    return buffer.getvalue()


def _make_universe(root: Path, count: int = 501):
    path = root / "data/raw/universe/nifty500_current.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["symbol", "company_name", "industry", "series", "isin"]
        )
        writer.writeheader()
        for index in range(count):
            writer.writerow({
                "symbol": f"S{index:03d}",
                "company_name": f"Company {index}",
                "industry": "Test",
                "series": "EQ",
                "isin": f"INE{index:06d}01010",
            })


def _make_existing_snapshot(root: Path, trade_date="2026-11-06", count=501):
    rows = []
    for index in range(count):
        row = {field: "" for field in FEATURE_COLUMNS}
        row.update({
            "trade_date": trade_date,
            "symbol": f"S{index:03d}",
            "company_name": f"Company {index}",
            "industry": "Test",
            "series": "EQ",
            "isin": f"INE{index:06d}01010",
            "fno_status": "unknown",
            "cash_data_status": "observed",
            "cash_close": "110",
            "cash_volume": "1000",
            "delivery_status": "reported",
            "delivery_percentage": "50",
            "futures_data_status": "no_contract_observation",
            "options_data_status": "no_contract_observation",
        })
        rows.append(row)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(FEATURE_COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode()
    digest = hashlib.sha256(payload).hexdigest()
    path = root / "data/snapshots/daily_stock_features" / (
        f"daily_stock_features_{trade_date}_{digest[:16]}.csv"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _historical_payloads(trade_date="2026-11-05"):
    cash_lines = [
        "TradDt,FinInstrmTp,ISIN,TckrSymb,SctySrs,ClsPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd"
    ]
    delivery_lines = [
        "SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,LAST_PRICE,CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,TURNOVER_LACS,NO_OF_TRADES,DELIV_QTY,DELIV_PER"
    ]
    for index in range(501):
        symbol = f"S{index:03d}"
        isin = f"INE{index:06d}01010"
        cash_lines.append(
            f"{trade_date},STK,{isin},{symbol},EQ,100,99,1000,100000,50"
        )
        delivery_lines.append(
            f"{symbol},EQ,05-Nov-2026,99,99,101,98,100,100,100,1000,10,50,500,50"
        )
    fo_lines = [
        "TradDt,FinInstrmTp,TckrSymb,XpryDt,OptnTp,ClsPric,PrvsClsgPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol"
    ]
    fo_lines.extend([
        f"{trade_date},STF,S000,2026-11-26,,100,99,1000,100,200",
        f"{trade_date},STO,S000,2026-11-26,CE,5,4,100,10,20",
        f"{trade_date},STO,S000,2026-11-26,PE,6,5,120,20,30",
    ])
    return {
        "cash_udiff": _zip_csv("\n".join(cash_lines) + "\n"),
        "cash_delivery_full": ("\n".join(delivery_lines) + "\n").encode(),
        "fo_udiff": _zip_csv("\n".join(fo_lines) + "\n"),
    }


def test_backfills_and_builds_a_complete_normalized_stock_snapshot(tmp_path, monkeypatch):
    from fii_engine import nse_historical_backfill as module

    _make_universe(tmp_path)
    _make_existing_snapshot(tmp_path)
    payloads = _historical_payloads()

    def fake_download(opener, source, trade_date):
        return payloads[source], f"https://example.invalid/{source}/{trade_date}"

    monkeypatch.setattr(module, "_download_archive", fake_download)
    result = run_backfill(
        tmp_path, sessions=2, max_calendar_days=3, as_of_date="2026-11-06"
    )

    assert result["status"] == "complete"
    assert result["sessions_available"] == 2
    assert result["universe_basis"].startswith("Current Nifty 500")
    assert result["historical_fno_membership"].startswith("unknown")
    created = next(item for item in result["created_snapshots"] if item["trade_date"] == "2026-11-05")
    snapshot = tmp_path / created["snapshot_path"]
    with snapshot.open(newline="", encoding="utf-8") as handle:
        rows = {row["symbol"]: row for row in csv.DictReader(handle)}
    assert len(rows) == 501
    assert rows["S000"]["cash_data_status"] == "observed"
    assert rows["S000"]["cash_close"] == "100"
    assert rows["S000"]["delivery_status"] == "reported"
    assert rows["S000"]["delivery_percentage"] == "50"
    assert rows["S000"]["fno_status"] == "unknown"
    assert rows["S000"]["futures_data_status"] == "observed"
    assert rows["S000"]["futures_buildup"] == "long_buildup"
    assert rows["S000"]["options_data_status"] == "observed"
    assert rows["S000"]["options_pcr_oi"] == "1.2"
    assert rows["S001"]["futures_data_status"] == "no_contract_observation"
    assert rows["S001"]["deal_event_count"] == ""
    assert (tmp_path / "data/raw/historical_backfill/backfill_manifest_current.json").is_file()


def test_backfill_does_not_treat_missing_archive_as_zero(tmp_path, monkeypatch):
    from fii_engine import nse_historical_backfill as module

    _make_universe(tmp_path)
    _make_existing_snapshot(tmp_path)

    monkeypatch.setattr(
        module, "_download_archive",
        lambda opener, source, trade_date: (None, f"https://example.invalid/{source}"),
    )
    with pytest.raises(RuntimeError, match="historical backfill incomplete"):
        run_backfill(
            tmp_path, sessions=2, max_calendar_days=2, as_of_date="2026-11-06"
        )
    assert not list((tmp_path / "data/snapshots/daily_stock_features").glob(
        "daily_stock_features_2026-11-05_*.csv"
    ))


def test_rejects_duplicate_delivery_symbol_series(tmp_path, monkeypatch):
    from fii_engine import nse_historical_backfill as module

    _make_universe(tmp_path)
    _make_existing_snapshot(tmp_path)
    payloads = _historical_payloads()
    delivery = payloads["cash_delivery_full"].decode()
    payloads["cash_delivery_full"] = (delivery + delivery.splitlines()[1] + "\n").encode()

    monkeypatch.setattr(
        module, "_download_archive",
        lambda opener, source, trade_date: (
            payloads[source], f"https://example.invalid/{source}/{trade_date}"
        ),
    )
    with pytest.raises(ValueError, match="duplicate delivery symbol/series"):
        run_backfill(
            tmp_path, sessions=2, max_calendar_days=2, as_of_date="2026-11-06"
        )
