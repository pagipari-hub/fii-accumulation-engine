from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from fii_engine.nsdl_cash_flow_normalizer import (
    normalize_dii_html,
    normalize_fpi_html,
    normalize_manifest,
)

FPI_HTML = """<table>
<tr><th>Reporting Date</th><th>Debt/Equity</th><th>Investment Route</th>
<th>Gross Purchases(Rs Crore)</th><th>Gross Sales(Rs Crore)</th>
<th>Net Investment (Rs Crore)</th><th>Net Investment US($) million</th></tr>
<tr><td>17-Aug-2026</td><td>Equity</td><td>Stock Exchange</td><td>100</td><td>80</td><td>20</td><td>2</td></tr>
<tr><td>Primary market &amp; others</td><td>5</td><td>1</td><td>4</td><td>0.4</td></tr>
<tr><td>Sub-total</td><td>105</td><td>81</td><td>24</td><td>2.4</td></tr>
<tr><td>18-Aug-2026</td><td>Debt-General Limit</td><td>Stock Exchange</td><td>50</td><td>60</td><td>(10)</td><td>(1)</td></tr>
<tr><td>Sub-total</td><td>50</td><td>60</td><td>(10)</td><td>(1)</td></tr>
<tr><td>Total for August</td><td>999</td><td>999</td><td>0</td><td>0</td></tr>
</table>"""

DII_HTML = """<table>
<tr><th>Type of DII</th><th>Instrument Type</th><th>Investment Route</th>
<th>Gross Purchases (Rs. Crore)</th><th>Gross Sales (Rs. Crore)</th>
<th>Net Investment (Rs. Crore)</th><th>Net Investment US ($) million</th></tr>
<tr><td>Bank</td><td>Equity</td><td>Stock Exchange</td><td>10</td><td>12</td><td>(2)</td><td>(0.2)</td></tr>
<tr><td>Primary market &amp; others</td><td>1</td><td>0</td><td>1</td><td>0.1</td></tr>
<tr><td></td><td></td><td>Bank-Total</td><td>11</td><td>12</td><td>(1)</td><td>(0.1)</td></tr>
<tr><td>Total</td><td>Equity</td><td>Stock Exchange</td><td>100</td><td>80</td><td>20</td><td>2</td></tr>
<tr><td>Primary market &amp; others</td><td>5</td><td>1</td><td>4</td><td>0.4</td></tr>
<tr><td>Equity Total</td><td>105</td><td>81</td><td>24</td><td>2.4</td></tr>
<tr><td>Grand-Total</td><td>Stock Exchange</td><td>999</td><td>999</td><td>0</td><td>0</td></tr>
</table>"""


def test_fpi_uses_equity_subtotal_not_debt_or_month_total() -> None:
    rows = normalize_fpi_html(FPI_HTML, "a" * 64, "2026-08-31")
    assert rows == [{
        "date": "2026-08-17",
        "fpi_gross_purchases_inr_crore": 105.0,
        "fpi_gross_sales_inr_crore": 81.0,
        "fpi_net_investment_inr_crore": 24.0,
        "fpi_source_query_date": "2026-08-31",
        "fpi_source_sha256": "a" * 64,
    }]


def test_dii_uses_explicit_equity_total_not_institution_subtotals() -> None:
    row = normalize_dii_html(DII_HTML, "b" * 64, "2026-08-17")
    assert row["dii_gross_purchases_inr_crore"] == 105.0
    assert row["dii_gross_sales_inr_crore"] == 81.0
    assert row["dii_net_investment_inr_crore"] == 24.0
    assert row["date"] == "2026-08-17"


def test_missing_dii_equity_total_fails_closed() -> None:
    with pytest.raises(ValueError, match="Equity Total"):
        normalize_dii_html(
            DII_HTML.replace("Equity Total", "Equity Sum"), "c" * 64, "2026-08-17"
        )


def test_manifest_hash_validation_and_csv_output(tmp_path: Path) -> None:
    fpi_path = tmp_path / "fpi" / "fpi.html"
    dii_path = tmp_path / "dii" / "dii.html"
    fpi_path.parent.mkdir()
    dii_path.parent.mkdir()
    fpi_bytes = FPI_HTML.encode()
    dii_bytes = DII_HTML.encode()
    fpi_path.write_bytes(fpi_bytes)
    dii_path.write_bytes(dii_bytes)
    manifest = {
        "start_date": "2026-08-17",
        "end_date": "2026-11-10",
        "observations": [
            {
                "dataset": "fpi",
                "query_date": "2026-08-31",
                "http_status": 200,
                "response_sha256": hashlib.sha256(fpi_bytes).hexdigest(),
                "raw_path": "fpi/fpi.html",
            },
            {
                "dataset": "dii",
                "query_date": "2026-08-17",
                "http_status": 200,
                "response_type": "data",
                "response_sha256": hashlib.sha256(dii_bytes).hexdigest(),
                "raw_path": "dii/dii.html",
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out.csv"
    result = normalize_manifest(manifest_path, output)
    assert result["fpi_unique_dates"] == 1
    assert result["dii_daily_rows"] == 1
    with output.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["fpi_net_investment_inr_crore"] == "24.0"
    assert rows[0]["dii_net_investment_inr_crore"] == "24.0"



def test_manifest_excludes_fpi_dates_outside_requested_window(tmp_path: Path) -> None:
    raw_path = tmp_path / "fpi" / "fpi.html"
    raw_path.parent.mkdir()
    raw_bytes = FPI_HTML.replace("17-Aug-2026", "16-Aug-2026").encode()
    raw_path.write_bytes(raw_bytes)
    manifest = {
        "start_date": "2026-08-17",
        "end_date": "2026-11-10",
        "observations": [
            {
                "dataset": "fpi",
                "query_date": "2026-08-31",
                "http_status": 200,
                "response_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "raw_path": "fpi/fpi.html",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out.csv"
    result = normalize_manifest(manifest_path, output)
    assert result["fpi_unique_dates"] == 0
    with output.open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == []
