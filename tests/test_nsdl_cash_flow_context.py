import csv
import hashlib
import json
from datetime import date, timedelta

import pytest

from fii_engine.nsdl_cash_flow_context import build_context


def _write_raw(root, relative, payload):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "raw_path": relative,
        "response_sha256": hashlib.sha256(payload).hexdigest(),
        "http_status": 200,
        "response_type": "data",
        "dataset": "fpi",
        "query_date": "2026-08-31",
    }


def test_nsdl_context_verifies_raw_sources_and_keeps_market_wide_scope(tmp_path):
    fpi_observation = _write_raw(
        tmp_path, "data/raw/nsdl_cash_flows/fpi/month.html", b"official fpi archive",
    )
    dii_observation = _write_raw(
        tmp_path, "data/raw/nsdl_cash_flows/dii/day.html", b"official dii archive",
    )
    no_data_payload = b"official NSDL No Data response"
    no_data_path = tmp_path / "data/raw/nsdl_cash_flows/dii/no_data.html"
    no_data_path.write_bytes(no_data_payload)
    no_data_observation = {
        "raw_path": "data/raw/nsdl_cash_flows/dii/no_data.html",
        "response_sha256": hashlib.sha256(no_data_payload).hexdigest(),
        "http_status": 200,
        "response_type": "no_data",
        "dataset": "dii",
        "query_date": "2026-08-26",
    }
    fpi_observation["dataset"] = "fpi"
    dii_observation["dataset"] = "dii"
    manifest = {
        "publisher": "National Securities Depository Limited",
        "scope": "official_nsdl_archive_probe",
        "start_date": "2026-08-10",
        "end_date": "2026-11-10",
        "fpi_query_count": 1,
        "dii_query_count": 2,
        "observations": [fpi_observation, dii_observation, no_data_observation],
    }
    manifest_path = tmp_path / "data/raw/nsdl_cash_flows/manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    csv_path = tmp_path / "data/processed/nsdl_cash_flows.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "reporting_date",
        "fpi_gross_purchases_inr_crore",
        "fpi_gross_sales_inr_crore",
        "fpi_net_investment_inr_crore",
        "dii_gross_purchases_inr_crore",
        "dii_gross_sales_inr_crore",
        "dii_net_investment_inr_crore",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        start = date(2026, 8, 10)
        for i in range(60):
            writer.writerow({
                "reporting_date": (start + timedelta(days=i)).isoformat(),
                "fpi_gross_purchases_inr_crore": "10",
                "fpi_gross_sales_inr_crore": "9",
                "fpi_net_investment_inr_crore": "1",
                "dii_gross_purchases_inr_crore": "20",
                "dii_gross_sales_inr_crore": "18",
                "dii_net_investment_inr_crore": "2",
            })

    context = build_context(tmp_path)
    assert context["normalized_date_count"] == 60
    assert context["fpi"]["cumulative_net_inr_crore"] == 60
    assert context["dii"]["cumulative_net_inr_crore"] == 120
    assert context["overlapping_fpi_dii_days"] == 60
    assert context["dii_no_data_days"] == ["2026-08-26"]
    assert context["stock_level_attribution"] is False
    assert context["raw_response_counts"] == {"data": 2, "no_data": 1}


def test_nsdl_context_rejects_raw_source_hash_mismatch(tmp_path):
    observation = _write_raw(
        tmp_path, "data/raw/nsdl_cash_flows/fpi/month.html", b"changed after manifest",
    )
    observation["response_sha256"] = "0" * 64
    manifest_path = tmp_path / "data/raw/nsdl_cash_flows/manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "observations": [observation],
        "start_date": "2026-08-10",
        "end_date": "2026-11-10",
    }), encoding="utf-8")
    csv_path = tmp_path / "data/processed/nsdl_cash_flows.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "reporting_date", "fpi_net_investment_inr_crore", "dii_net_investment_inr_crore",
        ])
        writer.writeheader()
        writer.writerow({
            "reporting_date": "2026-08-10",
            "fpi_net_investment_inr_crore": "1",
            "dii_net_investment_inr_crore": "1",
        })
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_context(tmp_path)
