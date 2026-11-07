import csv
import io
import zipfile

import pytest

from fii_engine.nse_historical_archive_probe import inspect_payload, run_probe


def _zip_csv(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.csv", text)
    return buffer.getvalue()


def test_inspects_cash_udiff_archive_and_validates_trade_date():
    payload = _zip_csv(
        "TradDt,ISIN,TckrSymb,SctySrs,ClsPric\n"
        "2026-11-06,INE000A01001,ABC,EQ,100.0\n"
    )
    result = inspect_payload(payload, "cash_udiff", "2026-11-06")
    assert result["row_count"] == 1
    assert result["observed_trade_dates"] == ["2026-11-06"]
    assert result["zip_member"] == "sample.csv"


def test_rejects_historical_archive_date_mismatch():
    payload = _zip_csv(
        "TradDt,FinInstrmTp,TckrSymb,XpryDt\n"
        "2026-10-30,STF,ABC,2026-11-26\n"
    )
    with pytest.raises(ValueError, match="expected only 2026-11-06"):
        inspect_payload(payload, "fo_udiff", "2026-11-06")


def test_inspects_full_cash_delivery_csv():
    payload = (
        "SYMBOL,SERIES,DATE1,CLOSE_PRICE,DELIV_QTY,DELIV_PER\n"
        "ABC,EQ,06-Nov-2026,100,5000,45.0\n"
    ).encode()
    result = inspect_payload(payload, "cash_delivery_full", "2026-11-06")
    assert result["row_count"] == 1
    assert "DELIV_PER" in result["columns"]


def test_rejects_missing_required_schema_field():
    payload = _zip_csv("TradDt,ISIN,TckrSymb,SctySrs\n2026-11-06,INE000A01001,ABC,EQ\n")
    with pytest.raises(ValueError, match="schema missing required fields"):
        inspect_payload(payload, "cash_udiff", "2026-11-06")


def test_probe_date_list_must_be_sorted_and_unique(tmp_path):
    with pytest.raises(ValueError, match="oldest to newest"):
        run_probe(tmp_path, ("2026-11-06", "2026-10-30"))
