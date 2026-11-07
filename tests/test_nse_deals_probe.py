import json

from fii_engine.nse_deals_probe import _csv_contract, _json_contract


def test_json_contract_extracts_columns_and_rows():
    payload = json.dumps({
        "columns": [{"title": "Symbol", "name": "symbol"}],
        "data": [{"symbol": "ABC"}],
    }).encode()
    result = _json_contract(payload)
    assert result["json_valid"] is True
    assert result["top_level_keys"] == ["columns", "data"]
    assert result["row_count"] == 1
    assert result["sample_rows"] == [{"symbol": "ABC"}]


def test_json_contract_preserves_error_payload():
    payload = json.dumps({"error": True, "showMessage": "No records"}).encode()
    result = _json_contract(payload)
    assert result["error"] is True
    assert result["showMessage"] == "No records"


def test_csv_contract_extracts_headers_and_sample():
    payload = b"Symbol,Date,Quantity\nABC,06-11-2026,100\n"
    result = _csv_contract(payload)
    assert result["columns"] == ["Symbol", "Date", "Quantity"]
    assert result["row_count"] == 1
    assert result["sample_rows"] == [["ABC", "06-11-2026", "100"]]


def test_csv_contract_marks_empty_body():
    result = _csv_contract(b"\xef\xbb\xbf")
    assert result["empty"] is True
    assert result["row_count"] == 0


def test_probe_uses_official_page_displayed_date_range():
    from fii_engine.nse_deals_probe import PAGE_DISPLAYED_FROM, PAGE_DISPLAYED_TO

    assert PAGE_DISPLAYED_FROM == "30-10-2026"
    assert PAGE_DISPLAYED_TO == "06-11-2026"
