from pathlib import Path

from fii_engine.daily_pipeline import _compact_result


def test_compact_collector_result_uses_repository_relative_paths(tmp_path):
    result = {
        "raw_path": str(tmp_path / "data/raw/source.csv"),
        "reconciliation": {
            "manifest_path": str(tmp_path / "data/snapshots/source.json"),
            "mismatch_count": 0,
        },
        "source_url": "https://www.nseindia.com/example",
    }
    compact = _compact_result(result, tmp_path)
    assert compact["raw_path"] == "data/raw/source.csv"
    assert compact["reconciliation"]["manifest_path"] == "data/snapshots/source.json"
    assert compact["source_url"] == "https://www.nseindia.com/example"
