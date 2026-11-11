"""Configuration and orchestration for the FII-first scoring model v0.2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fii_engine.accumulation_scoring import (
    CONFIRMATION_WEIGHTS,
    FII_WEIGHTS,
    score_fii_accumulation,
    score_market_confirmation,
)

MODEL_VERSION = "0.2-provisional"


def _weights_from_dimensions(config: dict, section: str) -> dict[str, int]:
    dimensions = config[section]["dimensions"]
    return {item["key"]: item["weight"] for item in dimensions}


def validate_config(config: dict) -> None:
    if config.get("model_version") != MODEL_VERSION:
        raise ValueError("unsupported scoring model version")
    primary = _weights_from_dimensions(config, "primary_score")
    confirmation = _weights_from_dimensions(config, "confirmation_score")
    if primary != FII_WEIGHTS:
        raise ValueError("primary FII score dimensions or weights do not match scoring engine")
    if confirmation != CONFIRMATION_WEIGHTS:
        raise ValueError("market confirmation dimensions or weights do not match scoring engine")
    for section, weights in (("primary_score", primary), ("confirmation_score", confirmation)):
        if any(not isinstance(value, int) or value < 0 for value in weights.values()):
            raise ValueError(f"{section}: weights must be non-negative integers")
        if sum(weights.values()) != 100:
            raise ValueError(f"{section}: weights must sum to 100")
    gates = config["primary_score"]["rankability_gates"]
    if not 0 < gates["minimum_coverage"] <= 1:
        raise ValueError("minimum FII score coverage must be in (0, 1]")
    if gates["minimum_observed_dimensions"] < 1:
        raise ValueError("minimum observed FII dimensions must be positive")
    if config["catalyst_assessment"]["display"] != "separate_bonus_not_added_to_fii_accumulation_score":
        raise ValueError("catalyst must remain separate from the FII accumulation score")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def score_stock(
    symbol: str,
    fii_observations: dict[str, dict[str, Any]],
    confirmation_observations: dict[str, dict[str, Any]],
    config: dict,
    *,
    identity_verified: bool,
    direct_gate_passed: bool,
    catalyst_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one stock while keeping FII, confirmation and catalyst separate."""
    validate_config(config)
    if not symbol or symbol.strip() != symbol:
        raise ValueError("symbol must be non-empty and have no surrounding whitespace")
    gates = config["primary_score"]["rankability_gates"]
    fii = score_fii_accumulation(
        fii_observations,
        identity_verified=identity_verified,
        direct_gate_passed=direct_gate_passed,
        minimum_coverage=gates["minimum_coverage"],
        minimum_observed_dimensions=gates["minimum_observed_dimensions"],
    )
    confirmation = score_market_confirmation(
        confirmation_observations,
        identity_verified=identity_verified,
        minimum_coverage=config["confirmation_score"].get("minimum_coverage", 0.60),
        minimum_observed_dimensions=config["confirmation_score"].get("minimum_observed_dimensions", 3),
    )
    catalyst = catalyst_assessment or {
        "status": "unavailable", "score": None,
        "summary": "No catalyst research feed supplied", "source_refs": [],
    }
    catalyst_status = catalyst.get("status", "observed")
    if catalyst_status not in {"observed", "unavailable"}:
        raise ValueError("catalyst status must be observed or unavailable")
    if catalyst_status == "unavailable":
        if catalyst.get("score") is not None:
            raise ValueError("unavailable catalyst score must be null")
    else:
        if isinstance(catalyst.get("score"), bool) or not isinstance(catalyst.get("score"), (int, float)):
            raise ValueError("observed catalyst score must be numeric")
        if not 0 <= catalyst["score"] <= 10:
            raise ValueError("catalyst score must be between 0 and 10")
        if not catalyst.get("source_refs"):
            raise ValueError("observed catalyst assessment requires source references")
    return {
        "symbol": symbol,
        "fii_accumulation": fii,
        "market_confirmation": confirmation,
        "catalyst": {
            "status": catalyst_status,
            "score": catalyst.get("score"),
            "summary": catalyst.get("summary", ""),
            "source_refs": catalyst.get("source_refs", []),
        },
        "model_version": config["model_version"],
        "note": "Catalyst is separate and is not added to the FII score or evidence grade.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/scoring.json"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    source = json.loads(args.input.read_text(encoding="utf-8"))
    rows = [
        score_stock(
            symbol,
            item["fii_observations"],
            item["confirmation_observations"],
            config,
            identity_verified=item.get("identity_verified", False),
            direct_gate_passed=item.get("direct_gate_passed", False),
            catalyst_assessment=item.get("catalyst"),
        )
        for symbol, item in source["stocks"].items()
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "model_version": config["model_version"], "rows": rows,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
