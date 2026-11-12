"""Validate and summarize official NSDL aggregate cash-flow history.

The output is market-wide context only and must never be attributed to individual stocks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_context(root: Path) -> dict:
    root = root.resolve()
    raw_manifest_path = root / "data/raw/nsdl_cash_flows/manifest.json"
    normalized_path = root / "data/processed/nsdl_cash_flows.csv"
    raw_manifest_payload = raw_manifest_path.read_bytes()
    raw_manifest = json.loads(raw_manifest_payload.decode("utf-8"))
    normalized_payload = normalized_path.read_bytes()
    normalized_sha = _sha256(normalized_payload)

    raw_status_counts = Counter()
    verified_raw_count = 0
    for observation in raw_manifest.get("observations", []):
        status = observation.get("response_type", "unknown")
        raw_status_counts[status] += 1
        raw_path = root / observation["raw_path"]
        if not raw_path.is_file():
            raise ValueError(f"NSDL raw response missing: {observation['raw_path']}")
        if _sha256(raw_path.read_bytes()) != observation.get("response_sha256"):
            raise ValueError(f"NSDL raw response SHA-256 mismatch: {observation['raw_path']}")
        if observation.get("http_status") != 200:
            raise ValueError(f"NSDL source response was not HTTP 200: {observation['raw_path']}")
        verified_raw_count += 1

    with normalized_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("NSDL normalized cash-flow CSV is empty")
    dates = [row["date"] for row in rows]
    if len(dates) != len(set(dates)):
        raise ValueError("NSDL normalized cash-flow CSV has duplicate dates")
    fpi_values = [
        float(row["fpi_net_investment_inr_crore"])
        for row in rows if row.get("fpi_net_investment_inr_crore", "").strip()
    ]
    dii_values = [
        float(row["dii_net_investment_inr_crore"])
        for row in rows if row.get("dii_net_investment_inr_crore", "").strip()
    ]
    overlap = sum(
        bool(row.get("fpi_net_investment_inr_crore", "").strip())
        and bool(row.get("dii_net_investment_inr_crore", "").strip())
        for row in rows
    )
    fpi_hashes = {
        observation.get("response_sha256")
        for observation in raw_manifest.get("observations", [])
        if observation.get("dataset") == "fpi" and observation.get("response_type") == "data"
    }
    dii_hashes = {
        observation.get("response_sha256")
        for observation in raw_manifest.get("observations", [])
        if observation.get("dataset") == "dii" and observation.get("response_type") == "data"
    }
    fpi_queries = {
        observation.get("query_date")
        for observation in raw_manifest.get("observations", [])
        if observation.get("dataset") == "fpi" and observation.get("response_type") == "data"
    }
    dii_queries = {
        observation.get("query_date")
        for observation in raw_manifest.get("observations", [])
        if observation.get("dataset") == "dii" and observation.get("response_type") == "data"
    }
    for row in rows:
        if row.get("fpi_source_query_date") not in fpi_queries:
            raise ValueError(f"NSDL FPI row has no matching raw query date: {row['date']}")
        if row.get("dii_source_query_date") not in dii_queries:
            raise ValueError(f"NSDL DII row has no matching raw query date: {row['date']}")
        if row.get("fpi_source_sha256") not in fpi_hashes:
            raise ValueError(f"NSDL FPI row lacks a matching raw response hash: {row['date']}")
        if row.get("dii_source_sha256") not in dii_hashes:
            raise ValueError(f"NSDL DII row lacks a matching raw response hash: {row['date']}")
    if len(fpi_values) < 60 or len(dii_values) < 60 or overlap < 60:
        raise ValueError("NSDL aggregate cash-flow history is below the 60-observation evidence floor")
    no_data_days = sorted(
        observation["query_date"]
        for observation in raw_manifest.get("observations", [])
        if observation.get("dataset") == "dii" and observation.get("response_type") == "no_data"
    )
    fpi_gross_buy = sum(float(row["fpi_gross_purchases_inr_crore"]) for row in rows if row.get("fpi_gross_purchases_inr_crore"))
    fpi_gross_sell = sum(float(row["fpi_gross_sales_inr_crore"]) for row in rows if row.get("fpi_gross_sales_inr_crore"))
    dii_gross_buy = sum(float(row["dii_gross_purchases_inr_crore"]) for row in rows if row.get("dii_gross_purchases_inr_crore"))
    dii_gross_sell = sum(float(row["dii_gross_sales_inr_crore"]) for row in rows if row.get("dii_gross_sales_inr_crore"))

    def stats(values: list[float]) -> dict:
        return {
            "observed_days": len(values),
            "cumulative_net_inr_crore": round(sum(values), 2),
            "positive_net_days": sum(value > 0 for value in values),
            "negative_net_days": sum(value < 0 for value in values),
            "zero_net_days": sum(value == 0 for value in values),
            "max_daily_net_inr_crore": round(max(values), 2),
            "min_daily_net_inr_crore": round(min(values), 2),
        }

    context = {
        "publisher": "National Securities Depository Limited",
        "scope": "aggregate_market_wide_cash_equity_context_only",
        "stock_level_attribution": False,
        "start_date": min(dates),
        "end_date": max(dates),
        "normalized_date_count": len(rows),
        "fpi": {
            **stats(fpi_values),
            "gross_purchases_inr_crore": round(fpi_gross_buy, 2),
            "gross_sales_inr_crore": round(fpi_gross_sell, 2),
        },
        "dii": {
            **stats(dii_values),
            "gross_purchases_inr_crore": round(dii_gross_buy, 2),
            "gross_sales_inr_crore": round(dii_gross_sell, 2),
        },
        "overlapping_fpi_dii_days": overlap,
        "dii_no_data_days": no_data_days,
        "raw_response_counts": dict(raw_status_counts),
        "verified_raw_response_count": verified_raw_count,
        "raw_archive_manifest_sha256": _sha256(raw_manifest_payload),
        "normalized_csv_sha256": normalized_sha,
        "raw_archive_manifest_path": "data/raw/nsdl_cash_flows/manifest.json",
        "normalized_csv_path": "data/processed/nsdl_cash_flows.csv",
        "source_boundaries": [
            "FPI rows are NSDL Equity Sub-total cash investment values; derivatives and debt are excluded.",
            "DII rows use NSDL Equity Total and do not sum investor/institution subtotals.",
            "Explicit No Data responses remain missing and are never converted to zero.",
            "These market-wide flows do not prove purchases or sales in any individual stock.",
        ],
    }
    return context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("data/processed/nsdl_cash_flow_context.json"))
    args = parser.parse_args()
    context = build_context(args.repo_root)
    output = args.repo_root / args.output if not args.output.is_absolute() else args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(context, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(context, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
