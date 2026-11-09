"""Adapt the current 501-stock evidence matrix into v0.2 score observations.

This first adapter intentionally scores only market confirmation. The current
matrix has no temporally verified FPI transaction quantities, no multi-week
FPI event history, and no comparable prior quarterly ownership snapshot.
Those FII dimensions therefore remain unavailable rather than being imputed.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable

from fii_engine.scoring import load_config, score_stock


MATRIX_PATH = Path("data/raw/fpi_evidence_blend/fpi_evidence_matrix_current.csv")
DEFAULT_OUTPUT = Path("data/raw/fpi_evidence_blend/fpi_scoring_current.json")

PRICE_WINDOWS = (5, 20, 60)


def _number(row: dict[str, str], key: str) -> float | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _percentile_ranks(values: list[float | None]) -> list[float | None]:
    """Return mid-rank empirical percentiles; ties receive the same rank."""
    indexed = [(value, index) for index, value in enumerate(values) if value is not None]
    if not indexed:
        return [None] * len(values)
    ordered = sorted(indexed, key=lambda item: item[0])
    result: list[float | None] = [None] * len(values)
    n = len(ordered)
    i = 0
    while i < n:
        j = i + 1
        while j < n and ordered[j][0] == ordered[i][0]:
            j += 1
        mid_rank = (i + j - 1) / 2
        percentile = 0.5 if n == 1 else mid_rank / (n - 1)
        for _, original_index in ordered[i:j]:
            result[original_index] = percentile
        i = j
    return result


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "score": None, "reason": reason}


def _observed(score: float, source_ref: str, reason: str) -> dict:
    return {
        "status": "observed",
        "score": min(1.0, max(0.0, float(score))),
        "source_refs": [source_ref],
        "reason": reason,
    }


def _read_matrix(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"matrix has no header: {path}")
        required = {
            "symbol", "isin", "as_of_trade_date", "historical_join_status",
            "5s_cash_return_pct", "20s_cash_return_pct", "60s_cash_return_pct",
            "5s_cash_return_status", "20s_cash_return_status", "60s_cash_return_status",
            "5s_average_delivery_percentage", "20s_average_delivery_percentage",
            "5s_delivery_status", "20s_delivery_status",
            "5s_average_daily_volume", "20s_average_daily_volume",
            "5s_volume_status", "20s_volume_status",
            "deal_temporally_verified_fpi_buy_quantity",
            "deal_temporally_verified_fpi_sell_quantity",
            "deal_registry_temporal_status",
        }
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"matrix missing required columns: {sorted(missing)}")
        rows = list(reader)
    symbols = [row["symbol"].strip().upper() for row in rows]
    if any(not symbol for symbol in symbols) or len(symbols) != len(set(symbols)):
        raise ValueError("matrix symbols must be non-empty and unique")
    return rows


def score_matrix_rows(
    rows: list[dict[str, str]],
    config: dict,
    *,
    matrix_ref: str,
) -> dict:
    if not rows:
        raise ValueError("matrix contains no stock rows")
    if not matrix_ref.strip():
        raise ValueError("matrix_ref is required for provenance")

    # Cross-sectional inputs are computed once over the same dated universe.
    delivery_delta: list[float | None] = []
    return_values: dict[int, list[float | None]] = {}
    volume_ratio: list[float | None] = []
    trend_values: list[list[float | None]] = []

    for row in rows:
        d5 = _number(row, "5s_average_delivery_percentage")
        d20 = _number(row, "20s_average_delivery_percentage")
        delivery_delta.append(
            d5 - d20
            if row.get("5s_delivery_status") == "complete"
            and row.get("20s_delivery_status") == "complete"
            and d5 is not None and d20 is not None
            else None
        )

        row_returns: list[float | None] = []
        for window in PRICE_WINDOWS:
            key = f"{window}s_cash_return_pct"
            status = row.get(f"{window}s_cash_return_status")
            value = _number(row, key)
            row_returns.append(value if status == "complete" else None)
        trend_values.append(row_returns)

        v5 = _number(row, "5s_average_daily_volume")
        v20 = _number(row, "20s_average_daily_volume")
        ratio = (
            v5 / v20
            if row.get("5s_volume_status") == "complete"
            and row.get("20s_volume_status") == "complete"
            and v5 is not None and v20 is not None and v5 > 0 and v20 > 0
            else None
        )
        volume_ratio.append(ratio)

    delivery_pct = _percentile_ranks(delivery_delta)
    return_pct_by_window = {
        index: _percentile_ranks([values[index] for values in trend_values])
        for index in range(len(PRICE_WINDOWS))
    }
    volume_pct = _percentile_ranks(volume_ratio)

    scored_rows = []
    for index, row in enumerate(rows):
        symbol = row["symbol"].strip().upper()
        source_ref = f"{matrix_ref}#symbol={symbol}"
        exact_identity = (
            row.get("historical_join_status") == "isin_exact"
            and bool(row.get("isin", "").strip())
        )

        # The present source matrix has no valid point-in-time direct FPI
        # observations. Even its four named-client leads have registration
        # intervals that are not verified for the transaction dates.
        temporal_status = row.get("deal_registry_temporal_status", "")
        direct_gate_passed = temporal_status == "temporally_verified"
        fii_observations = {
            "verified_net_fpi_transactions": _unavailable(
                "No temporally verified FPI buy/sell quantities are present in the current matrix."
            ),
            "persistence": _unavailable(
                "Only a current disclosed-deal snapshot is available; repeat weekly net FPI observations are not yet built."
            ),
            "quarterly_fpi_ownership_change": _unavailable(
                "A comparable prior identity-verified quarterly FPI ownership snapshot is not available in this matrix."
            ),
            "independent_fpi_buyer_breadth": _unavailable(
                "The matrix does not contain consolidated beneficial-owner group breadth for temporally verified net buyers."
            ),
            "relative_accumulation_intensity": _unavailable(
                "Temporally verified net FPI buy value is unavailable, so buying cannot be normalized by stock liquidity."
            ),
        }

        price_observations = [
            return_values.get(index) for index in range(len(PRICE_WINDOWS))
        ]
        valid_price_percentiles = [
            return_pct_by_window[i][index]
            for i in range(len(PRICE_WINDOWS))
            if trend_values[index][i] is not None
            and return_pct_by_window[i][index] is not None
        ]
        if len(valid_price_percentiles) >= 2:
            price_score = sum(valid_price_percentiles) / len(valid_price_percentiles)
            price_observation = _observed(
                price_score, source_ref,
                "Mean cross-sectional percentile across available 5/20/60-session returns; at least two complete windows required."
            )
        else:
            price_observation = _unavailable(
                "Fewer than two complete return horizons are available."
            )

        delivery_observation = (
            _observed(
                delivery_pct[index], source_ref,
                "Cross-sectional percentile of 5-session average delivery percentage minus 20-session baseline."
            )
            if delivery_pct[index] is not None else
            _unavailable("Complete 5-session and 20-session delivery observations are required.")
        )

        volume_observation = (
            _observed(
                volume_pct[index], source_ref,
                "Cross-sectional percentile of 5-session versus 20-session average daily volume ratio."
            )
            if volume_pct[index] is not None else
            _unavailable("Complete positive 5-session and 20-session average-volume observations are required.")
        )

        valid_returns = trend_values[index]
        if all(value is not None for value in valid_returns):
            positive = sum(value > 0 for value in valid_returns if value is not None)
            negative = sum(value < 0 for value in valid_returns if value is not None)
            nonzero = positive + negative
            if nonzero:
                positive_share = positive / len(PRICE_WINDOWS)
                positive_consistency = positive / nonzero
                trend_score = 0.7 * positive_share + 0.3 * positive_consistency * positive_share
                trend_observation = _observed(
                    trend_score, source_ref,
                    "Directional breadth proxy: rewards positive 5/20/60-session returns; not a volatility or drawdown measure."
                )
            else:
                trend_observation = _unavailable(
                    "All three return horizons are flat; directional trend quality is not measurable."
                )
        else:
            trend_observation = _unavailable(
                "Complete 5/20/60-session returns are required for the directional breadth proxy."
            )

        confirmation_observations = {
            "delivery_strength": delivery_observation,
            "relative_price_strength": price_observation,
            "volume_confirmation": volume_observation,
            "trend_stability": trend_observation,
        }

        result = score_stock(
            symbol,
            fii_observations,
            confirmation_observations,
            config,
            identity_verified=exact_identity,
            direct_gate_passed=direct_gate_passed,
            catalyst_assessment={
                "status": "unavailable",
                "score": None,
                "summary": "No dated catalyst research feed is connected to this matrix run.",
                "source_refs": [],
            },
        )
        result["isin"] = row.get("isin", "")
        result["source_as_of_date"] = row.get("as_of_trade_date", "")
        result["matrix_source_ref"] = source_ref
        result["evidence_grade"] = row.get("fpi_deal_evidence_grade", "")
        result["deal_temporal_status"] = temporal_status
        scored_rows.append(result)

    return {
        "model_version": config["model_version"],
        "model_status": "research_only_not_calibrated_not_a_production_watchlist",
        "matrix_source_ref": matrix_ref,
        "source_as_of_date": max(row.get("as_of_trade_date", "") for row in rows),
        "universe_row_count": len(rows),
        "fii_scoreable_row_count": sum(
            row["fii_accumulation"]["score"] is not None for row in scored_rows
        ),
        "fii_rankable_row_count": sum(
            row["fii_accumulation"]["rankable"] for row in scored_rows
        ),
        "confirmation_scoreable_row_count": sum(
            row["market_confirmation"]["score"] is not None for row in scored_rows
        ),
        "confirmation_rankable_row_count": sum(
            row["market_confirmation"]["rankable"] for row in scored_rows
        ),
        "rows": scored_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH)
    parser.add_argument("--config", type=Path, default=Path("config/scoring.json"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--matrix-ref", default="data/raw/fpi_evidence_blend/fpi_evidence_matrix_current.csv",
        help="Git commit/blob URL or manifest reference for matrix provenance",
    )
    args = parser.parse_args()

    rows = _read_matrix(args.matrix)
    config = load_config(args.config)
    result = score_matrix_rows(rows, config, matrix_ref=args.matrix_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
