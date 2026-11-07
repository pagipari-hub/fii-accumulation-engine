import json
from pathlib import Path

import pytest

from fii_engine.nse_fii_dii import ingest, parse_rows


def _payload(rows=None):
    if rows is None:
        rows = [
            {
                "date": "06-Nov-2026",
                "category": "FII/FPI *",
                "buyValue": "1,000.00",
                "sellValue": "800.00",
                "netValue": "200.00",
            },
            {
                "date": "06-Nov-2026",
                "category": "DII",
                "buyValue": "500.00",
                "sellValue": "600.00",
                "netValue": "-100.00",
            },
            {
                "date": "05-Nov-2026",
                "category": "FII/FPI",
                "buyValue": "900.00",
                "sellValue": "700.00",
                "netValue": "200.00",
            },
            {
                "date": "05-Nov-2026",
                "category": "DII",
                "buyValue": "400.00",
                "sellValue": "450.00",
                "netValue": "-50.00",
            },
        ]
    return json.dumps(rows).encode()


def test_parses_nse_only_fii_dii_rows_and_provisional_status():
    result = parse_rows(_payload())
    assert result["trade_dates"] == ["2026-11-05", "2026-11-06"]
    assert result["normalized_row_count"] == 4
    fii = next(row for row in result["rows"] if row["trade_date"] == "2026-11-06" and row["category"] == "FII/FPI")
    dii = next(row for row in result["rows"] if row["trade_date"] == "2026-11-06" and row["category"] == "DII")
    assert fii["buy_value_crore"] == "1000.00"
    assert fii["net_value_crore"] == "200.00"
    assert fii["publication_status"] == "provisional"
    assert dii["publication_status"] == "exchange_reported"
    assert result["scope"] == "NSE_only"


def test_rejects_missing_required_field():
    rows = [
        {
            "date": "06-Nov-2026", "category": "FII/FPI",
            "buyValue": "10", "sellValue": "5",
        }
    ]
    with pytest.raises(ValueError, match="missing fields"):
        parse_rows(_payload(rows))


def test_rejects_unknown_category():
    rows = [
        {
            "date": "06-Nov-2026", "category": "MF",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
        {
            "date": "06-Nov-2026", "category": "DII",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
    ]
    with pytest.raises(ValueError, match="unknown NSE FII/DII category"):
        parse_rows(_payload(rows))


def test_rejects_duplicate_date_category():
    rows = [
        {
            "date": "06-Nov-2026", "category": "FII/FPI",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
        {
            "date": "06-Nov-2026", "category": "FII/FPI *",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
        {
            "date": "06-Nov-2026", "category": "DII",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
    ]
    with pytest.raises(ValueError, match="duplicate NSE FII/DII row"):
        parse_rows(_payload(rows))


def test_rejects_date_missing_one_institution_category():
    rows = [
        {
            "date": "06-Nov-2026", "category": "FII/FPI",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        }
    ]
    with pytest.raises(ValueError, match="incomplete NSE FII/DII date"):
        parse_rows(_payload(rows))


def test_rejects_net_value_arithmetic_mismatch():
    rows = [
        {
            "date": "06-Nov-2026", "category": "FII/FPI",
            "buyValue": "10", "sellValue": "5", "netValue": "4",
        },
        {
            "date": "06-Nov-2026", "category": "DII",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
    ]
    with pytest.raises(ValueError, match="net arithmetic mismatch"):
        parse_rows(_payload(rows))


def test_missing_values_are_not_silently_converted_to_zero():
    rows = [
        {
            "date": "06-Nov-2026", "category": "FII/FPI",
            "buyValue": "", "sellValue": "5", "netValue": "-5",
        },
        {
            "date": "06-Nov-2026", "category": "DII",
            "buyValue": "10", "sellValue": "5", "netValue": "5",
        },
    ]
    with pytest.raises(ValueError, match="missing buyValue"):
        parse_rows(_payload(rows))


def test_ingest_writes_content_addressed_daily_snapshots_and_manifests(tmp_path):
    payload = _payload()
    first = ingest(tmp_path, raw_payload=payload)
    assert first["scope"] == "NSE_only"
    assert len(first["snapshots"]) == 2
    for snapshot in first["snapshots"]:
        snapshot_path = tmp_path / snapshot["snapshot_path"]
        manifest_path = tmp_path / snapshot["manifest_path"]
        assert snapshot_path.is_file()
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["snapshot_sha256"] == snapshot["snapshot_sha256"]
        assert manifest["scope"] == "NSE_only"
    raw_path = tmp_path / first["raw_response_path"]
    assert raw_path.read_bytes() == payload

    second = ingest(tmp_path, raw_payload=payload)
    assert [x["snapshot_path"] for x in first["snapshots"]] == [
        x["snapshot_path"] for x in second["snapshots"]
    ]
    assert len(list((tmp_path / "data/snapshots/fii_dii").glob("*.csv"))) == 2
