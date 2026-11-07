"""Deterministic storage layout for raw, processed and point-in-time data."""
from pathlib import Path

DATA_ROOT = Path("data")


def raw_path(dataset: str, partition: str) -> Path:
    """Path for immutable source artifacts, e.g. raw_path('cash_flows', '2026/11')."""
    return DATA_ROOT / "raw" / dataset / partition


def processed_path(dataset: str, partition: str) -> Path:
    """Path for normalized, validated datasets."""
    return DATA_ROOT / "processed" / dataset / partition


def snapshot_path(dataset: str, as_of: str) -> Path:
    """Path for point-in-time snapshots; as_of should be YYYY-MM-DD."""
    return DATA_ROOT / "snapshots" / dataset / as_of
