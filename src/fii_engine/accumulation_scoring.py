"""FII-first, missingness-aware scoring primitives.

This module scores validated observations only. Source adapters remain
responsible for temporal FPI identity, transaction netting and normalization.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

FII_WEIGHTS = {
    "verified_net_fpi_transactions": 35,
    "persistence": 25,
    "quarterly_fpi_ownership_change": 20,
    "independent_fpi_buyer_breadth": 10,
    "relative_accumulation_intensity": 10,
}
FII_DIRECT_DIMENSIONS = {
    "verified_net_fpi_transactions",
    "persistence",
    "independent_fpi_buyer_breadth",
    "relative_accumulation_intensity",
}
CONFIRMATION_WEIGHTS = {
    "delivery_strength": 35,
    "relative_price_strength": 30,
    "volume_confirmation": 20,
    "trend_stability": 15,
}


def _score_dimensions(
    observations: dict[str, dict[str, Any]],
    weights: dict[str, int],
    *,
    minimum_coverage: float,
    minimum_observed_dimensions: int,
    identity_verified: bool,
    direct_gate_passed: bool = True,
    direct_dimensions: set[str] | None = None,
) -> dict[str, Any]:
    """Calculate a weighted mean over observed dimensions, preserving N/A."""
    expected = set(weights)
    supplied = set(observations)
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        raise ValueError(f"dimension contract mismatch; missing={missing}, extra={extra}")

    observed_weight = 0
    applicable_weight = 0
    observed_count = 0
    weighted_total = 0.0
    details: dict[str, dict[str, Any]] = {}

    for key, weight in weights.items():
        item = observations[key]
        status = item.get("status")
        score = item.get("score")
        refs = item.get("source_refs") or []
        reason = str(item.get("reason") or "").strip()

        if status not in {"observed", "unavailable", "not_applicable"}:
            raise ValueError(f"{key}: invalid observation status {status!r}")
        if status == "observed":
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError(f"{key}: observed score must be numeric")
            if not isfinite(float(score)) or not 0 <= float(score) <= 1:
                raise ValueError(f"{key}: observed score must be finite and within [0, 1]")
            if not refs:
                raise ValueError(f"{key}: observed score requires source_refs")
            if direct_dimensions and key in direct_dimensions and not direct_gate_passed:
                raise ValueError(
                    f"{key}: direct FPI dimension cannot be scored before the "
                    "temporal-identity and transaction-reconciliation gate passes"
                )
            applicable_weight += weight
            observed_weight += weight
            observed_count += 1
            weighted_total += weight * float(score)
            details[key] = {
                "status": status, "score": float(score), "weight": weight,
                "weighted_points": weight * float(score), "source_refs": list(refs),
            }
        else:
            if score is not None:
                raise ValueError(f"{key}: unavailable/not_applicable score must be null")
            if not reason:
                raise ValueError(f"{key}: {status} observation requires a reason")
            if status == "unavailable":
                applicable_weight += weight
            details[key] = {
                "status": status, "score": None, "weight": weight,
                "reason": reason, "source_refs": list(refs),
            }

    score_value = (
        round(100.0 * weighted_total / observed_weight, 2)
        if observed_weight else None
    )
    coverage = (
        round(observed_weight / applicable_weight, 4)
        if applicable_weight else 0.0
    )
    rankable = (
        score_value is not None
        and coverage >= minimum_coverage
        and observed_count >= minimum_observed_dimensions
        and identity_verified
        and direct_gate_passed
    )
    if not identity_verified:
        status = "not_rankable_identity_unverified"
    elif not direct_gate_passed:
        status = "not_rankable_direct_fpi_gate_failed"
    elif score_value is None:
        status = "not_scored_no_observed_dimensions"
    elif not rankable:
        status = "not_rankable_insufficient_coverage_or_dimensions"
    else:
        status = "research_rankable_provisional"

    return {
        "score": score_value,
        "coverage": coverage,
        "coverage_pct": round(coverage * 100, 2),
        "observed_dimensions": observed_count,
        "observed_weight": observed_weight,
        "applicable_weight": applicable_weight,
        "rankable": rankable,
        "status": status,
        "dimensions": details,
    }


def score_fii_accumulation(
    observations: dict[str, dict[str, Any]],
    *,
    identity_verified: bool,
    direct_gate_passed: bool,
    minimum_coverage: float = 0.60,
    minimum_observed_dimensions: int = 3,
) -> dict[str, Any]:
    """Score stock-specific FPI accumulation evidence, never market-wide proxies."""
    return _score_dimensions(
        observations,
        FII_WEIGHTS,
        minimum_coverage=minimum_coverage,
        minimum_observed_dimensions=minimum_observed_dimensions,
        identity_verified=identity_verified,
        direct_gate_passed=direct_gate_passed,
        direct_dimensions=FII_DIRECT_DIMENSIONS,
    )


def score_market_confirmation(
    observations: dict[str, dict[str, Any]],
    *,
    identity_verified: bool,
    minimum_coverage: float = 0.60,
    minimum_observed_dimensions: int = 3,
) -> dict[str, Any]:
    """Score market confirmation separately from the FII accumulation score."""
    return _score_dimensions(
        observations,
        CONFIRMATION_WEIGHTS,
        minimum_coverage=minimum_coverage,
        minimum_observed_dimensions=minimum_observed_dimensions,
        identity_verified=identity_verified,
    )
