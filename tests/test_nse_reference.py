from fii_engine.nse_reference import (
    parse_catalog,
    select_cm_security_master,
    select_fo_contract_master,
)


def _record(**overrides):
    record = {
        "fileKey": "NSE-FO-CONTRACT-CSV",
        "fileActlName": "NSE_FO_contract_06112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.com/content/fo/",
        "tradingDate": "06-Nov-2026",
        "displayName": "F&O-MII – Contract File (.gz) (NSE Exclusive contract)",
        "fileSize": "1.87 MB",
        "filePosition": 28,
        "fileHashKey": "NSE-FO-CONTRACT-CSV-2",
    }
    record.update(overrides)
    return record


def _catalog(*records):
    return {"currentDate": "06-Nov-2026", "CurrentDay": list(records)}


def test_parse_catalog_requires_current_day_and_current_date():
    assert parse_catalog(b'{"currentDate":"06-Nov-2026","CurrentDay":[{}]}')["currentDate"] == "06-Nov-2026"


def test_parse_catalog_rejects_missing_current_day():
    import pytest

    with pytest.raises(ValueError, match="CurrentDay"):
        parse_catalog(b'{"currentDate":"06-Nov-2026"}')


def test_selects_exact_nse_exclusive_fo_contract_master():
    selected = select_fo_contract_master(_catalog(_record()))
    assert selected["file_key"] == "NSE-FO-CONTRACT-CSV"
    assert selected["filename"] == "NSE_FO_contract_06112026.csv.gz"
    assert selected["trading_date"] == "06-Nov-2026"


def test_fo_contract_master_rejects_wrong_trading_date():
    import pytest

    with pytest.raises(ValueError, match="does not match"):
        select_fo_contract_master(_catalog(_record(tradingDate="05-Nov-2026")))


def test_fo_contract_master_rejects_non_nse_archive_host():
    import pytest

    with pytest.raises(ValueError, match="untrusted archive path"):
        select_fo_contract_master(_catalog(_record(filePath="https://example.com/")))


def test_fo_contract_master_rejects_ambiguous_records():
    import pytest

    with pytest.raises(ValueError, match="exactly one"):
        select_fo_contract_master(_catalog(_record(), _record()))


def test_selects_nse_listed_cm_security_master_not_interop_variant():
    cm = {
        "fileKey": "CM-MII-SECURITY-CSV",
        "fileActlName": "NSE_CM_security_06112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.com/content/equities/",
        "tradingDate": "06-Nov-2026",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed securities)",
    }
    interop = {
        **cm,
        "filePath": "https://nsearchives.nseindia.com/content/equities/interop/",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed and BSE Exclusive securities)",
    }
    selected = select_cm_security_master(_catalog(interop, cm))
    assert selected["dataset"] == "nse_cm_security_master"
    assert "NSE Listed securities" in selected["display_name"]


def test_cm_security_master_rejects_missing_or_ambiguous_source():
    import pytest

    with pytest.raises(ValueError, match="exactly one"):
        select_cm_security_master(_catalog())
    cm = {
        "fileActlName": "NSE_CM_security_06112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.com/content/equities/",
        "tradingDate": "06-Nov-2026",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed securities)",
    }
    with pytest.raises(ValueError, match="exactly one"):
        select_cm_security_master(_catalog(cm, cm))
