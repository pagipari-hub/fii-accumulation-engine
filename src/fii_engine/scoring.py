"""Evidence-family scoring for the FII accumulation research engine.

This module combines pre-normalized, source-backed family observations. Source
adapters remain responsible for validating and normalizing raw market data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


HORIZONS = ("short_term", "positional")
VALID_STATUSES = {"observed", "unavailable", "not_applicable"}


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    if config.get("model_version") != "0.1-provisional":
        raise ValueError("unsupported scoring model version")
    horizons = config.get("horizons")
    if not isinstance(horizons, dict) or set(horizons) != set(HORIZONS):
        raise ValueError("scoring config must define short_term and positional horizons")
    for horizon, definition in horizons.items():
        weights = definition.get("family_weights")
        if not isinstance(weights, dict) or not weights:
            raise ValueError(f"{horizon}: family_weights must be a non-empty object")
        if any(not isinstance(v, (int, float)) or v < 0 for v in weights.values()):
            raise ValueError(f"{horizon}: family weights must be non-negative numbers")
        if abs(sum(weights.values()) - 100.0) > 1e-9:
            raise ValueError(f"{horizon}: family weights must sum to 100")
    thresholds = config.get("thresholds", {})
    required_thresholds = {
        "minimum_coverage", "minimum_observed_families",
        "grade_2_family_score", "grade_2_minimum_positive_families",
        "watchlist_score",
    }
    if not required_thresholds.issubset(thresholds):
        raise ValueError("scoring config is missing one or more required thresholds")
    for key in ("minimum_coverage", "grade_2_family_score", "watchlist_score"):
        value = thresholds[key]
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    for key in ("minimum_observed_families", "grade_2_minimum_positive_families"):
        value = thresholds[key]
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")


def _validate_observations(observations: list[dict], families: set[str]) -> dict[str, dict]:
    by_family = {}
    for item in observations:
        family = item.get("family")
        if family not in families:
            raise ValueError(f"unknown evidence family: {family!r}")
        if family in by_family:
            raise ValueError(f"duplicate evidence family observation: {family}")
        status = item.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"{family}: status must be one of {sorted(VALID_STATUSES)}")
        score = item.get("score")
        if status == "observed":
            if not isinstance(score, (int, float)) or not 0 <= score <= 1:
                raise ValueError(f"{family}: observed score must be between 0 and 1")
            refs = item.get("source_refs")
            if not isinstance(refs, list) or not refs or not all(
                isinstance(ref, str) and ref.strip() for ref in refs
            ):
                raise ValueError(f"{family}: observed evidence requires non-empty string source_refs")
        elif score is not None:
            raise ValueError(f"{family}: non-observed status must have score=null")
        if status in {"unavailable", "not_applicable"} and not (
            isinstance(item.get("reason"), str) and item["reason"].strip()
        ):
            raise ValueError(f"{family}: {status} observation requires a reason")
        by_family[family] = item
    return by_family


def _score_horizon(
    observations: dict[str, dict], weights: dict[str, float], config: dict
) -> dict:
    available_weight = 0.0
    applicable_weight = 0.0
    weighted_score = 0.0
    observed_families = []
    not_applicable_families = []
    unavailable_families = []
    breakdown = []

    for family, weight in weights.items():
        item = observations.get(family, {
            "family": family,
            "status": "unavailable",
            "score": None,
            "reason": "no observation supplied by the source pipeline",
            "source_refs": [],
        })
        status = item["status"]
        if status != "not_applicable":
            applicable_weight += weight
        if status == "observed":
            available_weight += weight
            weighted_score += weight * float(item["score"])
            observed_families.append(family)
        elif status == "unavailable":
            unavailable_families.append(family)
        else:
            not_applicable_families.append(family)
        breakdown.append({
            "family": family,
            "weight": weight,
            "status": status,
            "score": item["score"],
            "reason": item.get("reason"),
            "source_refs": item.get("source_refs", []),
        })

    score = weighted_score / available_weight if available_weight else None
    coverage = available_weight / applicable_weight if applicable_weight else None
    positive_threshold = config["thresholds"]["grade_2_family_score"]
    positive_families = [
        row["family"] for row in breakdown
        if row["status"] == "observed" and row["score"] >= positive_threshold
    ]
    minimum_coverage = config["thresholds"]["minimum_coverage"]
    minimum_families = config["thresholds"]["minimum_observed_families"]
    watchlist_score = config["thresholds"]["watchlist_score"]
    rankable = (
        score is not None
        and coverage is not None
        and coverage >= minimum_coverage
        and len(observed_families) >= minimum_families
        and score >= watchlist_score
    )
    return {
        "score": round(score * 100, 2) if score is not None else None,
        "coverage": round(coverage * 100, 2) if coverage is not None else None,
        "available_weight": round(available_weight, 2),
        "applicable_weight": round(applicable_weight, 2),
        "observed_family_count": len(observed_families),
        "positive_family_count": len(positive_families),
        "positive_families": positive_families,
        "observed_families": observed_families,
        "unavailable_families": unavailable_families,
        "not_applicable_families": not_applicable_families,
        "rankable": rankable,
        "family_breakdown": breakdown,
    }


def score_stock(
    symbol: str,
    observations: list[dict],
    config: dict,
    *,
    direct_fpi_transaction_refs: list[str] | None = None,
) -> dict:
    """Score one stock for both horizons without converting missing data to zero."""
    validate_config(config)
    all_families = set().union(
        *(set(definition["family_weights"]) for definition in config["horizons"].values())
    )
    if not symbol or symbol.strip() != symbol:
        raise ValueError("symbol must be non-empty and have no surrounding whitespace")
    if direct_fpi_transaction_refs is not None and (
        not isinstance(direct_fpi_transaction_refs, list)
        or not direct_fpi_transaction_refs
        or not all(isinstance(ref, str) and ref.strip() for ref in direct_fpi_transaction_refs)
    ):
        raise ValueError("Grade 1 transaction evidence requires non-empty source references")
    by_family = _validate_observations(observations, all_families)
    results = {
        horizon: _score_horizon(
            by_family, definition["family_weights"], config
        )
        for horizon, definition in config["horizons"].items()
    }
    strongest = max(
        (result["score"] for result in results.values() if result["score"] is not None),
        default=None,
    )
    positive_count = max(
        (result["positive_family_count"] for result in results.values()),
        default=0,
    )
    if direct_fpi_transaction_refs:
        grade = "Grade 1 - Confirmed"
    elif positive_count >= config["thresholds"]["grade_2_minimum_positive_families"]:
        grade = "Grade 2 - Corroborated"
    elif strongest is not None and strongest >= config["thresholds"]["watchlist_score"] * 100:
        grade = "Grade 3 - Early Watchlist"
    else:
        grade = "Unclassified - Insufficient positive evidence"

    return {
        "symbol": symbol,
        "evidence_grade": grade,
        "direct_fpi_transaction": bool(direct_fpi_transaction_refs),
        "direct_fpi_transaction_refs": direct_fpi_transaction_refs or [],
        "short_term": results["short_term"],
        "positional": results["positional"],
        "model_version": config["model_version"],
        "scoring_note": (
            "Scores are weighted averages of observed evidence families only. "
            "Coverage is reported separately; unavailable data is not a zero score."
        ),
    }


def score_universe(
    symbols: list[str],
    observations_by_symbol: dict[str, list[dict]],
    config: dict,
    direct_transactions: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Return every universe symbol, including those with no usable observations."""
    if len(symbols) != len(set(symbols)):
        raise ValueError("universe contains duplicate symbols")
    direct_transactions = direct_transactions or {}
    if any(not symbol or symbol.strip() != symbol for symbol in symbols):
        raise ValueError("universe symbols must be non-empty and have no surrounding whitespace")
    unknown = (set(observations_by_symbol) | set(direct_transactions)) - set(symbols)
    if unknown:
        raise ValueError(f"evidence contains symbols outside universe: {sorted(unknown)}")
    rows = [
        score_stock(
            symbol,
            observations_by_symbol.get(symbol, []),
            config,
            direct_fpi_transaction_refs=direct_transactions.get(symbol),
        )
        for symbol in symbols
    ]
    for horizon in HORIZONS:
        rows.sort(
            key=lambda row: (
                not row[horizon]["rankable"],
                -(row[horizon]["score"] if row[horizon]["score"] is not None else -1),
                row["symbol"],
            )
        )
        for rank, row in enumerate(rows, start=1):
            row[f"{horizon}_rank"] = rank if row[horizon]["rankable"] else None
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/scoring.json"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    source = json.loads(args.input.read_text(encoding="utf-8"))
    rows = score_universe(
        source["symbols"],
        source.get("observations_by_symbol", {}),
        config,
        set(source.get("direct_fpi_transactions", [])),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "model_version": config["model_version"],
        "rows": rows,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
