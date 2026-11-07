import csv
import hashlib
import json
from pathlib import Path

import pytest

from fii_engine.daily_stock_features import build_daily_features


TRADE_DATE = "2026-11-06"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_fixture(root: Path, *, include_sources: bool = True) -> None:
    universe_fields = ["symbol", "company_name", "industry", "series", "isin"]
    universe = [
        {"symbol": "ABC", "company_name": "ABC Ltd", "industry": "Banks", "series": "EQ", "isin": "INE000A01001"},
        {"symbol": "XYZ", "company_name": "XYZ Ltd", "industry": "IT", "series": "EQ", "isin": "INE000B01001"},
    ]
    for index in range(478):
        universe.append({
            "symbol": f"STOCK{index:03d}",
            "company_name": f"Stock {index}",
            "industry": "Other",
            "series": "EQ",
            "isin": f"INE{index + 1000:06d}01001",
        })
    write_csv(root / "data/raw/universe/nifty500_current.csv", universe_fields, universe)
    if not include_sources:
        return

    status_fields = ["symbol", "isin", "fno_status", "matched_fno_symbol"]
    status_rows = [
        {"symbol": row["symbol"], "isin": row["isin"],
         "fno_status": "eligible" if row["symbol"] == "ABC" else "not_eligible",
         "matched_fno_symbol": row["symbol"] if row["symbol"] == "ABC" else ""}
        for row in universe
    ]
    status_path = root / "data/raw/fno_universe/nifty500_fno_status_current.csv"
    status_hash = write_csv(status_path, status_fields, status_rows)
    write_json(root / "data/raw/fno_universe/nifty500_fno_status_current_manifest.json", {
        "output_sha256": status_hash,
        "fno_membership_retrieved_at_utc": "2026-11-06T16:00:00Z",
        "fno_membership_effective_date": None,
    })

    cash_fields = [
        "TradDt", "FinInstrmTp", "ISIN", "TckrSymb", "SctySrs", "ClsPric",
        "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd",
    ]
    cash_path = root / "data/processed/cash_market/cm.csv"
    cash_hash = write_csv(cash_path, cash_fields, [{
        "TradDt": TRADE_DATE, "FinInstrmTp": "STK", "ISIN": "INE000A01001",
        "TckrSymb": "ABC", "SctySrs": "EQ", "ClsPric": "105.00",
        "PrvsClsgPric": "100.00", "TtlTradgVol": "1000",
        "TtlTrfVal": "105000", "TtlNbOfTxsExctd": "100",
    }])
    delivery_fields = [
        "SYMBOL", "SERIES", "DATE1", "DELIV_QTY", "DELIV_PER",
        "TTL_TRD_QNTY",
    ]
    delivery_path = root / "data/raw/cash_market/delivery.csv"
    delivery_hash = write_csv(delivery_path, delivery_fields, [
        {"SYMBOL": "ABC", "SERIES": "EQ", "DATE1": "06-Nov-2026",
         "DELIV_QTY": "400", "DELIV_PER": "40.00", "TTL_TRD_QNTY": "1000"},
        {"SYMBOL": "XYZ", "SERIES": "EQ", "DATE1": "06-Nov-2026",
         "DELIV_QTY": "-", "DELIV_PER": "-", "TTL_TRD_QNTY": "1000"},
    ])
    write_json(root / "data/raw/cash_market/nse_cm_cash_current_manifest.json", {
        "trade_date": TRADE_DATE,
        "retrieved_at_utc": "2026-11-06T16:30:00Z",
        "datasets": [
            {"parsed_path": "data/processed/cash_market/cm.csv",
             "parsed_sha256": cash_hash, "raw_path": "data/raw/cash_market/cm.zip",
             "raw_sha256": hashlib.sha256(b"raw-cm").hexdigest(), "trade_date": TRADE_DATE},
            {"parsed_path": None, "raw_path": "data/raw/cash_market/delivery.csv",
             "raw_sha256": delivery_hash, "trade_date": TRADE_DATE},
        ],
    })
    (root / "data/raw/cash_market/cm.zip").write_bytes(b"raw-cm")

    fo_fields = [
        "TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "OptnTp", "ClsPric",
        "PrvsClsgPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol",
    ]
    fo_path = root / "data/processed/fno_bhavcopy/fo.csv"
    fo_rows = [
        {"TradDt": TRADE_DATE, "FinInstrmTp": "STF", "TckrSymb": "ABC",
         "XpryDt": "2026-11-23", "OptnTp": "", "ClsPric": "105",
         "PrvsClsgPric": "100", "OpnIntrst": "120", "ChngInOpnIntrst": "20",
         "TtlTradgVol": "500"},
        {"TradDt": TRADE_DATE, "FinInstrmTp": "STF", "TckrSymb": "ABC",
         "XpryDt": "2026-12-29", "OptnTp": "", "ClsPric": "106",
         "PrvsClsgPric": "100", "OpnIntrst": "200", "ChngInOpnIntrst": "10",
         "TtlTradgVol": "100"},
        {"TradDt": TRADE_DATE, "FinInstrmTp": "STO", "TckrSymb": "ABC",
         "XpryDt": "2026-11-23", "OptnTp": "CE", "ClsPric": "4",
         "PrvsClsgPric": "3", "OpnIntrst": "100", "ChngInOpnIntrst": "10",
         "TtlTradgVol": "20"},
        {"TradDt": TRADE_DATE, "FinInstrmTp": "STO", "TckrSymb": "ABC",
         "XpryDt": "2026-11-23", "OptnTp": "PE", "ClsPric": "5",
         "PrvsClsgPric": "4", "OpnIntrst": "200", "ChngInOpnIntrst": "20",
         "TtlTradgVol": "40"},
    ]
    fo_hash = write_csv(fo_path, fo_fields, fo_rows)
    write_json(root / "data/raw/fno_bhavcopy/nse_fo_udiff_current_manifest.json", {
        "trading_date": TRADE_DATE,
        "retrieved_at_utc": "2026-11-06T16:35:00Z",
        "finality": "final",
        "parsed_csv_path": "data/processed/fno_bhavcopy/fo.csv",
        "csv_sha256": fo_hash,
        "attribution_scope": "market_wide_contract_activity_not_participant_specific",
    })

    deal_fields = [
        "trade_date", "deal_type", "symbol", "client_name", "side", "quantity",
        "record_fingerprint",
    ]
    deal_path = root / "data/snapshots/bulk_block_deals/bulk_2026-11-06.csv"
    deal_hash = write_csv(deal_path, deal_fields, [{
        "trade_date": TRADE_DATE, "deal_type": "bulk", "symbol": "ABC",
        "client_name": "ABC FUND", "side": "BUY", "quantity": "500",
        "record_fingerprint": "fixture-deal-1",
    }])
    write_json(root / "data/raw/bulk_block_deals/current_manifest.json", {
        "requested_from": TRADE_DATE,
        "requested_to": TRADE_DATE,
        "retrieved_at_utc": "2026-11-06T16:40:00Z",
        "datasets": [{
            "deal_type": "bulk",
            "snapshots": [{
                "snapshot_path": "data/snapshots/bulk_block_deals/bulk_2026-11-06.csv",
                "snapshot_sha256": deal_hash,
                "trade_date": TRADE_DATE,
                "row_count": 1,
            }],
        }],
    })


def test_builds_full_universe_and_stock_specific_daily_metrics(tmp_path):
    build_fixture(tmp_path)
    manifest = build_daily_features(tmp_path)
    rows = list(csv.DictReader(
        (tmp_path / manifest["current_path"]).open(encoding="utf-8")
    ))
    assert len(rows) == 480
    abc = next(row for row in rows if row["symbol"] == "ABC")
    xyz = next(row for row in rows if row["symbol"] == "XYZ")
    assert abc["cash_data_status"] == "observed"
    assert abc["cash_return_pct"] == "5.00"
    assert abc["delivery_status"] == "reported"
    assert abc["delivery_percentage"] == "40.00"
    assert abc["futures_expiry"] == "2026-11-23"
    assert abc["futures_buildup"] == "long_buildup"
    assert abc["futures_oi_change_pct"] == "20"
    assert abc["options_pcr_oi"] == "2"
    assert abc["options_pcr_volume"] == "2"
    assert abc["deal_event_count"] == "1"
    assert abc["deal_counterparty_classification"] == "unclassified_not_FPI_attributed"
    assert abc["disclosed_deals_score_status"] == "unavailable"
    assert xyz["delivery_status"] == "not_reported"
    assert xyz["delivery_percentage"] == ""
    assert xyz["futures_data_status"] == "not_applicable"
    assert xyz["stock_derivatives_score_status"] == "not_applicable"
    assert manifest["counts"]["universe"] == 480
    assert manifest["scoring_status"] == "input_matrix_only_no_production_scores"


def test_missing_sources_remain_unavailable_and_all_symbols_are_kept(tmp_path):
    build_fixture(tmp_path, include_sources=False)
    manifest = build_daily_features(tmp_path)
    rows = list(csv.DictReader(
        (tmp_path / manifest["current_path"]).open(encoding="utf-8")
    ))
    assert len(rows) == 480
    abc = next(row for row in rows if row["symbol"] == "ABC")
    assert abc["cash_data_status"] == "unavailable"
    assert abc["fno_status"] == "unknown"
    assert abc["futures_data_status"] == "unavailable"
    assert abc["deal_event_count"] == ""
    assert abc["quarterly_ownership_score_status"] == "unavailable"


def test_fno_date_mismatch_is_not_blended_with_cash_data(tmp_path):
    build_fixture(tmp_path)
    fo_path = tmp_path / "data/processed/fno_bhavcopy/fo.csv"
    rows = list(csv.DictReader(fo_path.open(encoding="utf-8")))
    for row in rows:
        row["TradDt"] = "2026-11-05"
    fo_hash = write_csv(fo_path, list(rows[0]), rows)
    manifest_path = tmp_path / "data/raw/fno_bhavcopy/nse_fo_udiff_current_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["trading_date"] = "2026-11-05"
    manifest["csv_sha256"] = fo_hash
    write_json(manifest_path, manifest)
    result = build_daily_features(tmp_path)
    rows = list(csv.DictReader(
        (tmp_path / result["current_path"]).open(encoding="utf-8")
    ))
    abc = next(row for row in rows if row["symbol"] == "ABC")
    assert abc["trade_date"] == TRADE_DATE
    assert abc["futures_data_status"] == "unavailable"
    assert "dates do not match" in result["sources"]["fno_bhavcopy"]["reason"]


def test_rejects_source_hash_mismatch(tmp_path):
    build_fixture(tmp_path)
    manifest_path = tmp_path / "data/raw/fno_bhavcopy/nse_fo_udiff_current_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["csv_sha256"] = "0" * 64
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="source hash mismatch"):
        build_daily_features(tmp_path)


def test_rejects_fno_status_universe_mismatch(tmp_path):
    build_fixture(tmp_path)
    path = tmp_path / "data/raw/fno_universe/nifty500_fno_status_current.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows.pop()
    digest = write_csv(path, list(rows[0]), rows)
    manifest_path = tmp_path / "data/raw/fno_universe/nifty500_fno_status_current_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["output_sha256"] = digest
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="universe mismatch"):
        build_daily_features(tmp_path)
