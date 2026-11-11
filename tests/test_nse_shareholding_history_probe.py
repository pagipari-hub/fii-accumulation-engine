import json

from fii_engine.nse_shareholding_history_probe import _records


def test_records_extracts_list_from_symbol_history_response():
    payload = {"data": [{"symbol": "ALKEM", "date": "2026-09-30"}, {"symbol": "ALKEM", "date": "2026-06-30"}]}
    assert _records(payload) == payload["data"]


def test_records_accepts_direct_list_and_rejects_unexpected_shape():
    assert _records([{"symbol": "MCX"}]) == [{"symbol": "MCX"}]
    assert _records("not-json") == []
