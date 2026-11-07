import json
from datetime import date

import pytest

from fii_engine.nse_fo_underlyings import normalized_csv, parse_underlying_payload, reconcile_tracker_events


def _payload(stock_count=150):
    indexes = [
        {"symbol": f"INDEX{i}", "underlying": f"Index {i}", "serialNumber": i}
        for i in range(1, 7)
    ]
    stocks = [
        {"symbol": f"STOCK{i}", "underlying": f"Stock {i}", "serialNumber": i}
        for i in range(1, stock_count + 1)
    ]
    stocks[0]["symbol"] = "M&M"
    stocks[1]["symbol"] = "GVT&D"
    stocks[2]["symbol"] = "BAJAJ-AUTO"
    return json.dumps({"data": {"IndexList": indexes, "UnderlyingList": stocks}}).encode()


def test_parse_official_underlying_lists_and_preserve_special_symbols():
    indexes, stocks = parse_underlying_payload(_payload())
    assert len(indexes) == 6
    assert len(stocks) == 150
    assert [row["symbol"] for row in stocks[:3]] == ["M&M", "GVT&D", "BAJAJ-AUTO"]
    assert stocks[0]["segment"] == "individual_stock"


def test_parse_rejects_duplicate_symbols():
    body = json.loads(_payload().decode())
    body["data"]["UnderlyingList"][1]["symbol"] = body["data"]["UnderlyingList"][0]["symbol"]
    with pytest.raises(ValueError, match="duplicate symbols"):
        parse_underlying_payload(json.dumps(body).encode())


def test_parse_rejects_duplicate_or_non_contiguous_serial_numbers():
    body = json.loads(_payload().decode())
    body["data"]["UnderlyingList"][1]["serialNumber"] = 1
    with pytest.raises(ValueError, match="duplicate serial"):
        parse_underlying_payload(json.dumps(body).encode())


def test_parse_rejects_unexpected_stock_count():
    body = json.loads(_payload(stock_count=149).decode())
    with pytest.raises(ValueError, match="outside safety range"):
        parse_underlying_payload(json.dumps(body).encode())


def test_parse_rejects_missing_underlying_list():
    with pytest.raises(ValueError, match="UnderlyingList"):
        parse_underlying_payload(b'{"data":{"IndexList":[{}]}}')


def test_normalized_csv_contains_index_and_stock_segments():
    indexes, stocks = parse_underlying_payload(_payload())
    output = normalized_csv(indexes, stocks).decode()
    assert output.splitlines()[0] == "segment,serial_number,symbol,underlying"
    assert output.count("\n") == 157
    assert "individual_stock,1,M&M,Stock 1" in output


def test_tracker_reconciliation_applies_official_symbol_alias():
    events = [
        {"action": "introduction", "symbol": "ZOMATO", "effective_date": "2024-11-29"},
        {"action": "introduction", "symbol": "IEX", "effective_date": "2024-11-29"},
        {"action": "exclusion", "symbol": "IEX", "effective_date": "2026-11-25"},
    ]
    aliases = [{
        "old_symbol": "ZOMATO",
        "new_symbol": "ETERNAL",
        "effective_date": "2025-04-09",
    }]
    result = reconcile_tracker_events(
        events, {"ETERNAL", "IEX"}, aliases, date(2026, 11, 7)
    )
    assert result["status"] == "pass"
    assert result["mismatch_count"] == 0
    assert result["future_event_count"] == 1
    assert result["aliases_applied"][0]["new_symbol"] == "ETERNAL"


def test_tracker_reconciliation_reports_unexplained_membership_mismatch():
    events = [
        {"action": "introduction", "symbol": "ABC", "effective_date": "2024-11-29"}
    ]
    result = reconcile_tracker_events(events, set(), [], date(2026, 11, 7))
    assert result["status"] == "mismatch"
    assert result["mismatch_count"] == 1
    assert result["mismatches"][0]["symbol"] == "ABC"


def test_symbol_alias_is_not_applied_before_its_effective_date():
    events = [
        {"action": "introduction", "symbol": "ZOMATO", "effective_date": "2024-11-29"}
    ]
    aliases = [{
        "old_symbol": "ZOMATO",
        "new_symbol": "ETERNAL",
        "effective_date": "2025-04-09",
    }]
    result = reconcile_tracker_events(
        events, {"ZOMATO"}, aliases, date(2025, 4, 8)
    )
    assert result["status"] == "pass"
    assert result["aliases_applied"] == []
