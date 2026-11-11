import csv
import gzip
import json

from fii_engine.nse_mii_identity_reconciliation import reconcile


def test_reconciles_dated_mii_symbol_isin_without_releasing_ownership(tmp_path):
    mii = tmp_path / "mii.csv.gz"
    with gzip.open(mii, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["TckrSymb", "SctySrs", "ISIN", "FinInstrmId", "FinInstrmNm", "DelFlg", "ListgDt", "RmvlDt"],
        )
        writer.writeheader()
        writer.writerow({"TckrSymb": "ABC", "SctySrs": "EQ", "ISIN": "INE000A01001", "FinInstrmId": "1", "FinInstrmNm": "ABC Limited", "DelFlg": "N"})
        writer.writerow({"TckrSymb": "ABC", "SctySrs": "BE", "ISIN": "INE000A01002", "FinInstrmId": "2", "FinInstrmNm": "ABC Limited", "DelFlg": "N"})
    candidates = tmp_path / "candidates.csv"
    with candidates.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["reporting_date", "symbol", "universe_isin", "master_isin", "master_company_name", "master_record_id"])
        writer.writeheader()
        writer.writerow({"reporting_date": "2026-09-30", "symbol": "ABC", "universe_isin": "INE000A01001", "master_isin": "INE000A01009", "master_company_name": "ABC Limited", "master_record_id": "1"})
    report = reconcile(mii, candidates, tmp_path / "out")
    assert report["counts"] == {"dated_mii_matches_universe_isin": 1}
    assert report["identity_safety"]["no_isin_override_performed"] is True
    row = next(csv.DictReader((tmp_path / "out" / "shareholding_mii_identity_reconciliation.csv").open()))
    assert row["mii_isin"] == "INE000A01001"
    assert row["master_isin"] == "INE000A01009"


def test_ambiguous_eq_symbol_is_not_resolved(tmp_path):
    mii = tmp_path / "mii.csv.gz"
    with gzip.open(mii, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["TckrSymb", "SctySrs", "ISIN", "DelFlg"])
        writer.writeheader()
        writer.writerow({"TckrSymb": "ABC", "SctySrs": "EQ", "ISIN": "INE000A01001", "DelFlg": "N"})
        writer.writerow({"TckrSymb": "ABC", "SctySrs": "EQ", "ISIN": "INE000A01002", "DelFlg": "N"})
    candidates = tmp_path / "candidates.csv"
    with candidates.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["reporting_date", "symbol", "universe_isin", "master_isin", "master_company_name", "master_record_id"])
        writer.writeheader()
        writer.writerow({"reporting_date": "2026-09-30", "symbol": "ABC", "universe_isin": "INE000A01001", "master_isin": "INE000A01009", "master_company_name": "ABC Limited", "master_record_id": "1"})
    report = reconcile(mii, candidates, tmp_path / "out")
    assert report["counts"] == {"ambiguous_eq_symbol_match": 1}
