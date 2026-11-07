import csv
import json
from datetime import date
from pathlib import Path

from fii_engine.cdsl_fpi_registry import normalize_registry_name
from fii_engine.fpi_deal_classifier import (
    _classify_event,
    _deduplicate_cross_report_rows,
    _stock_summary,
    _temporal_status,
)


def _row(deal_type, client, side="BUY", quantity="100", price="10.00", remarks=""):
    return {
        "trade_date": "2026-11-06",
        "deal_type": deal_type,
        "symbol": "ABC",
        "security_name": "ABC LIMITED",
        "client_name": client,
        "side": side,
        "quantity": quantity,
        "weighted_average_price": price,
        "remarks": remarks,
        "record_fingerprint": f"{deal_type}-{client}-{side}-{quantity}-{remarks}",
        "_source_file": f"{deal_type}_snapshot.csv",
    }


def test_cross_report_duplicate_bulk_block_is_counted_once():
    rows = [
        _row("bulk", "SOCIETE GENERALE", remarks="-"),
        _row("block", "SOCIETE GENERALE", remarks=""),
    ]
    events = _deduplicate_cross_report_rows(rows)
    assert len(events) == 1
    assert events[0]["cross_report_duplicate"] == "true"
    assert events[0]["source_record_count"] == 2
    assert events[0]["source_report_types"] == "block;bulk"


def test_repeated_same_report_rows_are_not_silently_collapsed():
    rows = [
        _row("bulk", "SOCIETE GENERALE"),
        _row("bulk", "SOCIETE GENERALE"),
    ]
    events = _deduplicate_cross_report_rows(rows)
    assert len(events) == 2
    assert all(event["cross_report_duplicate"] == "false" for event in events)


def test_registry_match_distinguishes_direct_fpi_from_odi_and_unmatched():
    registry = {
        normalize_registry_name("SOCIETE GENERALE"): [{
            "registered_name": "SOCIETE GENERALE",
            "registration_no": "INFRFP020917",
            "valid_upto_date": "2029-01-27",
        }],
        normalize_registry_name("GOLDMAN SACHS BANK EUROPE SE - ODI"): [{
            "registered_name": "GOLDMAN SACHS BANK EUROPE SE - ODI",
            "registration_no": "INGEFP058125",
            "valid_upto_date": "2028-05-28",
        }],
    }
    direct = _classify_event({
        "event_id": "1", "trade_date": "2026-11-06", "client_name": "SOCIETE GENERALE",
        "client_normalized_name": normalize_registry_name("SOCIETE GENERALE"),
    }, registry, date(2026, 11, 6))
    odi = _classify_event({
        "event_id": "2", "trade_date": "2026-11-06", "client_name": "GOLDMAN SACHS BANK EUROPE SE - ODI",
        "client_normalized_name": normalize_registry_name("GOLDMAN SACHS BANK EUROPE SE - ODI"),
    }, registry, date(2026, 11, 6))
    unknown = _classify_event({
        "event_id": "3", "trade_date": "2026-11-06", "client_name": "TENCENT CLOUD EUROPE B.V.",
        "client_normalized_name": normalize_registry_name("TENCENT CLOUD EUROPE B.V."),
    }, registry, date(2026, 11, 6))
    assert direct["counterparty_class"] == "current_registry_matched_fpi_client"
    assert direct["temporal_status"] == "observed_in_registry_on_trade_date"
    assert odi["counterparty_class"] == "current_registry_matched_odi_issuer"
    assert unknown["counterparty_class"] == "unmatched"
    assert unknown["temporal_status"] == "not_established"


def test_post_trade_registry_snapshot_does_not_prove_historical_start_date():
    assert _temporal_status(
        date(2026, 10, 30), date(2026, 11, 8), ["2027-05-05"]
    ) == "post_trade_snapshot_registration_start_unknown"


def test_nomura_legal_suffix_variant_matches_deterministically():
    client = "NOMURA FUNDS IRELAND PLC NOMURA FUNDS IRELAND INDIA EQUITY FUND"
    registered = "NOMURA FUNDS IRELAND PUBLIC LIMITED COMPANY - NOMURA FUNDS IRELAND - INDIA EQUITY FUND"
    assert normalize_registry_name(client) == normalize_registry_name(registered)


def test_stock_summary_keeps_unmatched_and_odi_out_of_direct_fpi_net():
    events = [
        {
            "symbol": "ABC", "side": "BUY", "quantity": "100", "deal_value": "1000.00",
            "counterparty_class": "registered_fpi_client", "temporal_status": "post_trade_snapshot_registration_start_unknown",
            "source_record_count": 1, "cross_report_duplicate": "false",
        },
        {
            "symbol": "ABC", "side": "BUY", "quantity": "50", "deal_value": "500.00",
            "counterparty_class": "odi_issuer", "temporal_status": "post_trade_snapshot_registration_start_unknown",
            "source_record_count": 1, "cross_report_duplicate": "false",
        },
        {
            "symbol": "ABC", "side": "SELL", "quantity": "25", "deal_value": "250.00",
            "counterparty_class": "unmatched", "temporal_status": "not_established",
            "source_record_count": 1, "cross_report_duplicate": "false",
        },
    ]
    result = _stock_summary(events, date(2026, 10, 30), date(2026, 11, 6))[0]
    assert result["current_registry_matched_fpi_client_buy_quantity"] == "100"
    assert result["odi_issuer_buy_quantity"] == "50"
    assert result["unmatched_sell_quantity"] == "25"
    assert result["current_registry_matched_fpi_client_known_net_quantity"] == "100"
    assert result["known_fpi_net_status"] == "partial_unmatched_or_odi_counterparties"
