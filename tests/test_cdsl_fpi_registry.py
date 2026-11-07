from fii_engine.cdsl_fpi_registry import (
    _registry_metrics,
    normalize_registry_name,
    parse_registry_html,
    parse_valid_upto,
)


def test_parse_registry_html_extracts_entity_and_registration_fields():
    payload = b"""
    <html><body>
      <table>
        <tr><td>1. Name</td><td>BNP PARIBAS FINANCIAL MARKETS</td></tr>
        <tr><td>Registration No.</td><td>INFRFP085515</td></tr>
        <tr><td>Registration valid upto</td><td>05-05-2027 00:00:00</td></tr>
        <tr><td>Country Name</td><td>FRANCE</td></tr>
        <tr><td>Address</td><td>PARIS</td></tr>
      </table>
    </body></html>
    """
    assert parse_registry_html(payload) == [{
        "registered_name": "BNP PARIBAS FINANCIAL MARKETS",
        "registration_no": "INFRFP085515",
        "valid_upto_raw": "05-05-2027 00:00:00",
        "country": "FRANCE",
        "address": "PARIS",
    }]


def test_registry_name_normalization_is_deterministic_not_fuzzy():
    assert normalize_registry_name(
        "Morgan Stanley Asia Singapore Pte"
    ) == normalize_registry_name(
        "MORGAN STANLEY ASIA (SINGAPORE) PTE."
    )
    assert normalize_registry_name(
        "Nomura Funds Ireland PLC Nomura Funds Ireland India Equity Fund"
    ) == normalize_registry_name(
        "Nomura Funds Ireland Public Limited Company - Nomura Funds Ireland - India Equity Fund"
    )
    assert normalize_registry_name(
        "NOMURA FUNDS IRELAND INDIA EQUITY FUND"
    ) != normalize_registry_name(
        "NOMURA FUNDS IRELAND ASIA EQUITY FUND"
    )


def test_parse_valid_upto_preserves_unknown_as_missing():
    assert parse_valid_upto("05-05-2027 00:00:00") == "2027-05-05"
    assert parse_valid_upto("") is None
    assert parse_valid_upto("unknown") is None


def test_registry_metrics_preserve_duplicate_registration_numbers_and_names():
    records = [
        {"registration_no": "INFRFP000001", "normalized_name": "FUND A",
         "valid_upto_raw": "01-01-2028", "valid_upto_date": "2028-01-01"},
        {"registration_no": "INFRFP000001", "normalized_name": "FUND B",
         "valid_upto_raw": "01-01-2028", "valid_upto_date": "2028-01-01"},
        {"registration_no": "INFRFP000002", "normalized_name": "FUND A",
         "valid_upto_raw": "", "valid_upto_date": ""},
    ]
    metrics = _registry_metrics(records)
    assert metrics["record_count"] == 3
    assert metrics["unique_registration_number_count"] == 2
    assert metrics["unique_normalized_name_count"] == 2
    assert metrics["duplicate_registration_number_excess"] == 1
    assert metrics["duplicate_normalized_name_excess"] == 1
    assert metrics["registration_numbers_with_multiple_names"] == 1
    assert metrics["normalized_names_with_multiple_registration_numbers"] == 1
