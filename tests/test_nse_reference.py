from fii_engine.nse_reference import (
    _save_catalogue_payload,
    parse_catalog,
    inspect_cm_quality,
    inspect_fo_quality,
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
    assert selected["catalogue_group"] == "CurrentDay"


def test_selects_fo_contract_master_from_previous_day_and_excludes_interop():
    record = _record(
        filePath="https://nsearchives.nseindia.in/content/fo/",
        tradingDate="17-Nov-2026",
        fileActlName="NSE_FO_contract_17112026.csv.gz",
    )
    interop = _record(
        fileKey="NSEBSE-FO-CONTRACT-CSV",
        filePath="https://nsearchives.nseindia.in/content/fo/interop/",
        tradingDate="17-Nov-2026",
        fileActlName="NSE_FO_contract_17112026.csv.gz",
        displayName="F&O-MII – Contract File (.gz) (NSE and BSE Exclusive Contract)",
    )
    selected = select_fo_contract_master({
        "currentDate": "18-Nov-2026",
        "CurrentDay": [],
        "PreviousDay": [interop, record],
    })
    assert selected["trading_date"] == "17-Nov-2026"
    assert selected["catalogue_group"] == "PreviousDay"


def test_fo_contract_master_rejects_ambiguous_latest_exact_file_key():
    import pytest

    record = _record(
        tradingDate="17-Nov-2026",
        fileActlName="NSE_FO_contract_17112026.csv.gz",
    )
    with pytest.raises(ValueError, match="exactly one latest"):
        select_fo_contract_master({
            "currentDate": "18-Nov-2026",
            "CurrentDay": [record],
            "PreviousDay": [record],
        })


def test_fo_contract_master_rejects_wrong_trading_date():
    import pytest

    with pytest.raises(ValueError, match="catalogue/filename date mismatch"):
        select_fo_contract_master(_catalog(_record(tradingDate="05-Nov-2026")))


def test_fo_contract_master_rejects_non_nse_archive_host():
    import pytest

    with pytest.raises(ValueError, match="untrusted archive path"):
        select_fo_contract_master(_catalog(_record(filePath="https://example.com/")))


def test_fo_contract_master_rejects_ambiguous_records():
    import pytest

    with pytest.raises(ValueError, match="exactly one"):
        select_fo_contract_master(_catalog(_record(), _record()))


def test_selects_nse_listed_cm_security_master_from_previous_day_not_interop():
    cm = {
        "fileKey": "NSE-CM-SECURITY-CSV",
        "fileActlName": "NSE_CM_security_17112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.in/content/cm/",
        "tradingDate": "17-Nov-2026",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed securities)",
    }
    interop = {
        **cm,
        "fileKey": "BSE-CM-SECURITY-CSV",
        "filePath": "https://nsearchives.nseindia.in/content/cm/interop/",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed and BSE Exclusive securities)",
    }
    catalog = {
        "currentDate": "18-Nov-2026",
        "CurrentDay": [],
        "PreviousDay": [interop, cm],
    }
    selected = select_cm_security_master(catalog)
    assert selected["dataset"] == "nse_cm_security_master"
    assert selected["trading_date"] == "17-Nov-2026"
    assert selected["catalogue_group"] == "PreviousDay"
    assert "NSE Listed securities" in selected["display_name"]


def test_cm_master_rejects_ambiguous_latest_exact_file_key():
    import pytest

    cm = {
        "fileKey": "NSE-CM-SECURITY-CSV",
        "fileActlName": "NSE_CM_security_17112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.in/content/cm/",
        "tradingDate": "17-Nov-2026",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed securities)",
    }
    with pytest.raises(ValueError, match="exactly one latest"):
        select_cm_security_master({
            "currentDate": "18-Nov-2026",
            "CurrentDay": [cm],
            "PreviousDay": [cm],
        })


def test_mii_catalogue_filename_date_must_match_trading_date():
    import pytest

    cm = {
        "fileKey": "NSE-CM-SECURITY-CSV",
        "fileActlName": "NSE_CM_security_16112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.in/content/cm/",
        "tradingDate": "17-Nov-2026",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed securities)",
    }
    with pytest.raises(ValueError, match="catalogue/filename date mismatch"):
        select_cm_security_master({
            "currentDate": "18-Nov-2026",
            "CurrentDay": [],
            "PreviousDay": [cm],
        })


def test_cm_security_master_rejects_missing_or_ambiguous_source():
    import pytest

    with pytest.raises(ValueError, match="no NSE-CM-SECURITY-CSV"):
        select_cm_security_master(_catalog())
    cm = {
        "fileKey": "NSE-CM-SECURITY-CSV",
        "fileActlName": "NSE_CM_security_06112026.csv.gz",
        "filePath": "https://nsearchives.nseindia.com/content/cm/",
        "tradingDate": "06-Nov-2026",
        "displayName": "CM - MII - Security File (.gz) (NSE Listed securities)",
    }
    with pytest.raises(ValueError, match="exactly one latest"):
        select_cm_security_master({
            "currentDate": "07-Nov-2026",
            "CurrentDay": [cm],
            "PreviousDay": [cm],
        })


def test_cm_master_decodes_record_update_with_mii_epoch():
    payload = (
        "TckrSymb,SctySrs,ISIN,DelFlg,UpdDt\n"
        "HDFCBANK,EQ,INE040A01034,N,1478457391\n"
    ).encode()
    result = inspect_cm_quality(payload, "06-Nov-2026")
    assert result["latest_record_update_date_utc"] == "2026-11-06"
    assert result["record_update_timestamp_semantics"] == (
        "row_level_local_database_update_not_file_publication_time"
    )
    assert result["catalogue_trading_date"] == "2026-11-06"
    assert result["issues"] == []


def test_cm_master_quarantines_missing_active_security_identity():
    import pytest

    payload = (
        "TckrSymb,SctySrs,ISIN,DelFlg,UpdDt\n"
        ",EQ,,N,1478457391\n"
    ).encode()
    result = inspect_cm_quality(payload, "06-Nov-2026")
    assert result["active_rows_missing_identity"] == 1
    assert any("missing identity fields" in issue for issue in result["issues"])


def test_fo_master_does_not_count_dummy_future_stock_contracts():
    payload = (
        "FinInstrmNm,TckrSymb,XpryDt,DelFlg,PrtdToTrad,TradgStsNrmlMkt,ElgbltyNrmlMkt\n"
        "OPTSTK,HDFCBANK,1200000000,N,1,2,1\n"
        "FUTSTK,011NSETEST,1795876200,N,0,2,1\n"
    ).encode()
    result = inspect_fo_quality(payload, "06-Nov-2026")
    assert result["stock_contract_rows"] == 1
    assert result["stock_contract_rows_expiring_on_or_after_catalogue_date"] == 0
    assert result["issues"]


def test_fo_master_accepts_non_test_future_tradable_stock_contract():
    payload = (
        "FinInstrmNm,TckrSymb,XpryDt,DelFlg,PrtdToTrad,TradgStsNrmlMkt,ElgbltyNrmlMkt\n"
        "FUTSTK,HDFCBANK,1485301800,N,1,2,1\n"
    ).encode()
    result = inspect_fo_quality(payload, "06-Nov-2026")
    assert result["latest_stock_expiry_date_utc"] == "2027-01-25"
    assert result["stock_contract_rows_expiring_on_or_after_catalogue_date"] == 1
    assert result["issues"] == []


def test_fo_contract_master_accepts_nseindia_in_archive_host():
    selected = select_fo_contract_master(_catalog(_record(
        filePath="https://nsearchives.nseindia.in/content/fo/"
    )))
    assert selected["download_url"].startswith(
        "https://nsearchives.nseindia.in/content/fo/"
    )



def test_catalogue_payload_is_preserved_content_addressed_and_repeatable(tmp_path):
    import hashlib

    payload = b'{"currentDate":"06-Nov-2026","CurrentDay":[{"fileKey":"test"}]}'
    first = _save_catalogue_payload(
        tmp_path, "cm", "https://www.nseindia.com/api/daily-reports?key=CM", payload
    )
    second = _save_catalogue_payload(
        tmp_path, "cm", "https://www.nseindia.com/api/daily-reports?key=CM", payload
    )
    assert first == second
    saved = tmp_path / first["raw_path"]
    assert saved.read_bytes() == payload
    assert first["raw_sha256"] == hashlib.sha256(payload).hexdigest()
    assert first["raw_bytes"] == len(payload)


def test_mii_epoch_is_1980_not_unix_1970():
    from fii_engine.nse_reference import _mii_timestamp_date

    assert _mii_timestamp_date(1483021800).isoformat() == "2026-12-29"
