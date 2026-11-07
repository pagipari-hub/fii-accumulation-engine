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
    _universe_membership,
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
            "counterparty_class": "current_registry_matched_fpi_client", "temporal_status": "post_trade_snapshot_registration_start_unknown",
            "source_record_count": 1, "cross_report_duplicate": "false",
        },
        {
            "symbol": "ABC", "side": "BUY", "quantity": "50", "deal_value": "500.00",
            "counterparty_class": "current_registry_matched_odi_issuer", "temporal_status": "post_trade_snapshot_registration_start_unknown",
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
    assert result["current_registry_matched_odi_issuer_buy_quantity"] == "50"
    assert result["unmatched_sell_quantity"] == "25"
    assert result["current_registry_matched_fpi_client_known_net_quantity"] == "100"
    assert result["known_fpi_net_status"] == "partial_unmatched_or_odi_counterparties"


def test_alias_match_and_multiple_registration_matches_are_explicit():
    client = "NOMURA FUNDS IRELAND PLC NOMURA FUNDS IRELAND INDIA EQUITY FUND"
    registered = "NOMURA FUNDS IRELAND PUBLIC LIMITED COMPANY - NOMURA FUNDS IRELAND - INDIA EQUITY FUND"
    alias_registry = {
        normalize_registry_name(registered): [{
            "registered_name": registered,
            "registration_no": "INIRFP235015",
            "valid_upto_date": "2027-11-10",
        }]
    }
    alias = _classify_event({
        "event_id": "alias", "trade_date": "2026-11-06", "client_name": client,
        "client_normalized_name": normalize_registry_name(client),
    }, alias_registry, date(2026, 11, 8))
    assert alias["registry_match_status"] == "current_registry_match"
    assert alias["name_match_type"] == "legal_suffix_normalized"
    assert alias["temporal_status"] == "post_trade_snapshot_registration_start_unknown"

    duplicate_registry = {
        normalize_registry_name("FUND ALPHA"): [
            {"registered_name": "FUND ALPHA", "registration_no": "INUSFP000001", "valid_upto_date": "2028-01-01"},
            {"registered_name": "FUND ALPHA", "registration_no": "INUSFP000002", "valid_upto_date": "2028-01-01"},
        ]
    }
    ambiguous = _classify_event({
        "event_id": "ambiguous", "trade_date": "2026-11-06", "client_name": "FUND ALPHA",
        "client_normalized_name": normalize_registry_name("FUND ALPHA"),
    }, duplicate_registry, date(2026, 11, 8))
    assert ambiguous["registry_match_status"] == "multiple_registration_numbers"
    assert ambiguous["counterparty_class"] == "current_registry_match_ambiguous_registration"




def test_nifty500_membership_requires_exact_symbol_and_company_name():
    row = {
        "symbol": "ABC",
        "company_name": "ABC Limited",
        "industry": "Industrials",
        "series": "EQ",
        "isin": "INE000A01001",
    }
    by_isin = {"INE000A01001": row}
    by_symbol = {"ABC": row}
    by_name = {normalize_registry_name("ABC Limited"): [row]}
    exact = _universe_membership({
        "symbol": "ABC", "security_name": "ABC Ltd.", "source_isin": ""
    }, by_isin, by_symbol, by_name)
    assert exact["nifty500_snapshot_membership_status"] == "nifty500_symbol_company_name_exact_normalized_match"
    assert exact["nifty500_snapshot_member_verified"] == "true"
    assert exact["matched_universe_isin"] == "INE000A01001"
    assert exact["universe_join_method"] == "symbol_and_company_name_exact_normalized"

    mismatch = _universe_membership({
        "symbol": "ABC", "security_name": "XYZ Industries Limited", "source_isin": ""
    }, by_isin, by_symbol, by_name)
    assert mismatch["nifty500_snapshot_membership_status"] == "symbol_match_company_name_mismatch"
    assert mismatch["nifty500_snapshot_member_verified"] == "false"

    source_isin = _universe_membership({
        "symbol": "ABC", "security_name": "ABC Limited", "source_isin": "INE000A01001"
    }, by_isin, by_symbol, by_name)
    assert source_isin["nifty500_snapshot_membership_status"] == "nifty500_isin_symbol_company_match"
    assert source_isin["nifty500_snapshot_member_verified"] == "true"

    outside = _universe_membership({
        "symbol": "OUTSIDE", "security_name": "Outside Corp", "source_isin": ""
    }, by_isin, by_symbol, by_name)
    assert outside["nifty500_snapshot_membership_status"] == "outside_current_nifty500"

def test_same_day_expired_registration_is_not_treated_as_active():
    assert _temporal_status(
        date(2026, 11, 6), date(2026, 11, 6), ["2026-11-05"]
    ) == "expired_on_trade_date"


def test_classifier_writes_auditable_outputs_and_deduplicates_cross_report(tmp_path):
    import csv
    import hashlib
    import json

    from fii_engine.fpi_deal_classifier import classify

    registry_dir = tmp_path / "data/raw/cdsl_fpi_registry"
    registry_dir.mkdir(parents=True)
    registry_csv = tmp_path / "data/snapshots/cdsl_fpi_registry/test.csv"
    registry_csv.parent.mkdir(parents=True)
    registry_rows = [{
        "registered_name": "SOCIETE GENERALE",
        "normalized_name": normalize_registry_name("SOCIETE GENERALE"),
        "registration_no": "INFRFP020917",
        "valid_upto_raw": "27-01-2029 00:00:00",
        "valid_upto_date": "2029-01-27",
        "country": "FRANCE",
        "address": "PARIS",
        "source_letter": "S",
        "source_url": "https://www.cdslindia.com/Publications/RegForeignInstInvstr.aspx?indexname=S",
        "source_page_sha256": "pagehash",
        "retrieved_at_utc": "2026-11-06T18:00:00+00:00",
    }]
    with registry_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(registry_rows[0]))
        writer.writeheader()
        writer.writerows(registry_rows)
    registry_hash = hashlib.sha256(registry_csv.read_bytes()).hexdigest()
    (registry_dir / "cdsl_fpi_registry_current_manifest.json").write_text(json.dumps({
        "status": "complete",
        "successful_letter_pages": 26,
        "retrieved_date_ist": "2026-11-06",
        "normalized_csv_path": "data/snapshots/cdsl_fpi_registry/test.csv",
        "normalized_csv_sha256": registry_hash,
    }))

    universe_path = tmp_path / "data/raw/universe/nifty500_current.csv"
    universe_path.parent.mkdir(parents=True)
    universe_rows = [{
        "symbol": "ABC", "company_name": "ABC Limited", "industry": "Industrials",
        "series": "EQ", "isin": "INE000A01001",
    }]
    for index in range(500):
        universe_rows.append({
            "symbol": "TEST" + str(index).zfill(3),
            "company_name": "Test Company " + str(index),
            "industry": "Test",
            "series": "EQ",
            "isin": "INE" + str(index + 1).zfill(9),
        })
    with universe_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(universe_rows[0]))
        writer.writeheader()
        writer.writerows(universe_rows)

    manifest_dir = tmp_path / "data/raw/bulk_block_deals/manifests"
    manifest_dir.mkdir(parents=True)
    snapshots_dir = tmp_path / "data/snapshots/bulk_block_deals"
    snapshots_dir.mkdir(parents=True)
    fields = [
        "trade_date", "deal_type", "symbol", "security_name", "client_name",
        "side", "quantity", "weighted_average_price", "remarks", "record_fingerprint",
    ]
    for deal_type, remarks in [("bulk", "-"), ("block", "")]:
        snapshot = snapshots_dir / f"{deal_type}_2026-11-06.csv"
        row = {
            "trade_date": "2026-11-06", "deal_type": deal_type, "symbol": "ABC",
            "security_name": "ABC LIMITED", "client_name": "SOCIETE GENERALE",
            "side": "BUY", "quantity": "100", "weighted_average_price": "10.00",
            "remarks": remarks, "record_fingerprint": f"{deal_type}-fingerprint",
        }
        with snapshot.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
        snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        (manifest_dir / f"{deal_type}_2026-11-06.json").write_text(json.dumps({
            "deal_type": deal_type,
            "trade_date": "2026-11-06",
            "retrieved_at_utc": "2026-11-06T18:30:00+00:00",
            "snapshot_path": str(snapshot.relative_to(tmp_path)),
            "snapshot_sha256": snapshot_hash,
        }))

    result = classify(tmp_path, lookback_days=7)
    assert result["source_row_count"] == 2
    assert result["deduplicated_event_count"] == 1
    assert result["cross_report_duplicate_event_count"] == 1
    event_csv = tmp_path / result["current_event_path"]
    with event_csv.open(encoding="utf-8", newline="") as handle:
        event_rows = list(csv.DictReader(handle))
    assert event_rows[0]["counterparty_class"] == "current_registry_matched_fpi_client"
    assert event_rows[0]["temporal_status"] == "observed_in_registry_on_trade_date"
    stock_csv = tmp_path / result["current_stock_summary_path"]
    with stock_csv.open(encoding="utf-8", newline="") as handle:
        stock_rows = list(csv.DictReader(handle))
    assert stock_rows[0]["current_registry_matched_fpi_client_buy_quantity"] == "100"
    assert stock_rows[0]["nifty500_snapshot_member_verified"] == "true"
    assert event_rows[0]["source_isin"] == ""
    assert event_rows[0]["matched_universe_isin"] == "INE000A01001"
    assert event_rows[0]["universe_join_method"] == "symbol_and_company_name_exact_normalized"
    assert event_rows[0]["nifty500_snapshot_membership_status"] == "nifty500_symbol_company_name_exact_normalized_match"
    assert result["universe_row_count"] == 501
    assert result["nifty500_snapshot_verified_event_count"] == 1
