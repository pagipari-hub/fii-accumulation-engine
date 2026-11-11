from __future__ import annotations

import pytest

from fii_engine.accumulation_scoring import (
    CONFIRMATION_WEIGHTS,
    FII_WEIGHTS,
    score_fii_accumulation,
    score_market_confirmation,
)


def _observations(weights, *, observed=None):
    observed = observed or {}
    return {
        key: (
            {"status": "observed", "score": observed[key],
             "source_refs": [f"source:{key}"]}
            if key in observed else
            {"status": "unavailable", "score": None, "reason": "source history unavailable"}
        )
        for key in weights
    }


def test_fii_score_is_weighted_and_coverage_is_separate():
    observations = _observations(
        FII_WEIGHTS,
        observed={
            "verified_net_fpi_transactions": 1.0,
            "persistence": 0.8,
            "quarterly_fpi_ownership_change": 0.6,
        },
    )
    result = score_fii_accumulation(
        observations, identity_verified=True, direct_gate_passed=True
    )
    assert result["score"] == 83.75
    assert result["coverage_pct"] == 80.0
    assert result["rankable"] is True
    assert result["status"] == "research_rankable_provisional"


def test_unverified_temporal_identity_cannot_score_direct_dimensions():
    observations = _observations(
        FII_WEIGHTS,
        observed={"verified_net_fpi_transactions": 0.9},
    )
    with pytest.raises(ValueError, match="cannot be scored"):
        score_fii_accumulation(
            observations, identity_verified=True, direct_gate_passed=False
        )


def test_missing_dimensions_reduce_coverage_not_score():
    observations = _observations(
        FII_WEIGHTS,
        observed={
            "verified_net_fpi_transactions": 0.7,
            "persistence": 0.7,
            "quarterly_fpi_ownership_change": 0.7,
        },
    )
    observations["independent_fpi_buyer_breadth"] = {
        "status": "not_applicable", "score": None,
        "reason": "no separately identifiable buyer groups in this validated feed",
    }
    result = score_fii_accumulation(
        observations, identity_verified=True, direct_gate_passed=True
    )
    assert result["score"] == 70.0
    assert result["coverage_pct"] == 88.89


def test_missingness_is_not_zero_and_observed_requires_provenance():
    observations = _observations(FII_WEIGHTS)
    observations["quarterly_fpi_ownership_change"] = {
        "status": "observed", "score": 0.0, "source_refs": []
    }
    with pytest.raises(ValueError, match="requires source_refs"):
        score_fii_accumulation(
            observations, identity_verified=True, direct_gate_passed=True
        )


def test_unverified_universe_identity_blocks_rankability():
    observations = _observations(
        FII_WEIGHTS,
        observed={
            "verified_net_fpi_transactions": 1.0,
            "persistence": 1.0,
            "quarterly_fpi_ownership_change": 1.0,
        },
    )
    result = score_fii_accumulation(
        observations, identity_verified=False, direct_gate_passed=True
    )
    assert result["score"] == 100.0
    assert result["rankable"] is False
    assert result["status"] == "not_rankable_identity_unverified"


def test_market_confirmation_is_a_separate_weighted_score():
    observations = _observations(
        CONFIRMATION_WEIGHTS,
        observed={
            "delivery_strength": 1.0,
            "relative_price_strength": 0.5,
            "volume_confirmation": 0.5,
        },
    )
    result = score_market_confirmation(observations, identity_verified=True)
    assert result["score"] == 70.59
    assert result["coverage_pct"] == 85.0
    assert result["rankable"] is True


def test_observed_score_must_be_within_zero_and_one():
    observations = _observations(
        CONFIRMATION_WEIGHTS, observed={"delivery_strength": 1.1}
    )
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        score_market_confirmation(observations, identity_verified=True)
