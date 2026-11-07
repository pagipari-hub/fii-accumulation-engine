"""Run official source collectors and build the daily Nifty 500 evidence matrix."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fii_engine import (
    fno_universe,
    nifty500_universe,
    nse_bulk_block_deals,
    nse_cash_market,
    nse_fii_dii,
    nse_fo_aggregate_reports,
    nse_fo_bhavcopy,
    nse_fo_underlyings,
)
from fii_engine.daily_stock_features import build_daily_features


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _portable(value, root: Path):
    if isinstance(value, dict):
        return {key: _portable(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable(item, root) for item in value]
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return value
    return value


def _compact_result(result, root: Path):
    if not isinstance(result, dict):
        return str(result)
    compact = {}
    for key, value in result.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[key] = _portable(value, root)
        elif isinstance(value, dict) and key in {
            "counts", "summary", "reconciliation", "validation",
        }:
            compact[key] = _portable(value, root)
        elif isinstance(value, list):
            compact[f"{key}_count"] = len(value)
    return compact


def run_pipeline(root: Path) -> dict:
    root = root.resolve()
    collectors = [
        ("nifty500_universe", nifty500_universe.ingest),
        ("nse_fo_underlyings", nse_fo_underlyings.ingest),
        ("nifty500_fno_status", fno_universe.ingest),
        ("nse_fii_dii", nse_fii_dii.ingest),
        ("nse_cash_market", nse_cash_market.ingest),
        ("nse_fo_bhavcopy", nse_fo_bhavcopy.ingest),
        ("nse_fo_aggregate_reports", nse_fo_aggregate_reports.ingest),
        ("nse_bulk_block_deals", nse_bulk_block_deals.ingest),
    ]
    statuses = {}
    for name, collector in collectors:
        started = _utc_now()
        try:
            result = collector(root)
            statuses[name] = {
                "status": "success",
                "started_at_utc": started,
                "completed_at_utc": _utc_now(),
                "result": _compact_result(result, root),
            }
        except Exception as exc:  # one feed must not suppress independent evidence
            statuses[name] = {
                "status": "failure",
                "started_at_utc": started,
                "completed_at_utc": _utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

    core_available = any(
        statuses.get(name, {}).get("status") == "success"
        for name in ("nse_cash_market", "nse_fo_bhavcopy")
    )
    all_succeeded = all(item["status"] == "success" for item in statuses.values())
    pipeline_status = (
        "complete" if all_succeeded
        else "partial" if core_available
        else "no_market_data"
    )
    statuses["pipeline"] = {
        "status": pipeline_status,
        "generated_at_utc": _utc_now(),
        "core_market_source_available": core_available,
    }

    status_path = root / "data/raw/daily_stock_features/collector_run_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        matrix = build_daily_features(root, collector_status=statuses)
    except Exception as exc:
        status_payload = {
            "pipeline_status": "matrix_build_failed",
            "generated_at_utc": _utc_now(),
            "collectors": statuses,
            "matrix_error_type": type(exc).__name__,
            "matrix_error": str(exc),
        }
        status_path.write_text(
            json.dumps(status_payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        raise

    status_payload = {
        "pipeline_status": pipeline_status,
        "generated_at_utc": _utc_now(),
        "collectors": statuses,
        "matrix": {
            "trade_date": matrix["trade_date"],
            "row_count": matrix["output_row_count"],
            "output_sha256": matrix["output_sha256"],
            "snapshot_path": matrix["snapshot_path"],
            "scoring_status": matrix["scoring_status"],
        },
    }
    status_path.write_text(
        json.dumps(status_payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return status_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    result = run_pipeline(args.repo_root)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if result["pipeline_status"] == "no_market_data":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
