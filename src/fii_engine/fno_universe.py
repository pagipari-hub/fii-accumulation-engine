"""Join the official Nifty 500 universe to NSE's current stock F&O underlying list."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from io import StringIO
from pathlib import Path


def _read_csv(path: Path, required: set[str]) -> tuple[bytes, list[dict[str, str]]]:
    if not path.is_file():
        raise FileNotFoundError(f"required source file missing: {path}")
    payload = path.read_bytes()
    try:
        rows = list(csv.DictReader(StringIO(payload.decode("utf-8-sig"))))
    except UnicodeDecodeError as exc:
        raise ValueError(f"source CSV is not UTF-8: {path}") from exc
    if not rows or not required.issubset(set(rows[0])):
        raise ValueError(f"source CSV schema drift: {path}")
    return payload, rows


def _find_manifest(directory: Path, normalized_sha256: str) -> tuple[Path, dict]:
    for path in directory.glob("*.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("normalized_sha256") == normalized_sha256:
            return path, manifest
    raise FileNotFoundError(
        f"no provenance manifest in {directory} matches normalized hash {normalized_sha256}"
    )


def build_status_rows(
    nifty_rows: list[dict[str, str]],
    fno_symbols: set[str],
    aliases: list[dict],
    fno_snapshot_date: date,
) -> tuple[list[dict], dict]:
    if not nifty_rows or not fno_symbols:
        raise ValueError("Nifty 500 rows and current F&O symbols are both required")
    nifty_symbols = [row.get("symbol", "").strip() for row in nifty_rows]
    if any(not symbol for symbol in nifty_symbols):
        raise ValueError("Nifty 500 source contains a blank symbol")
    if len(nifty_symbols) != len(set(nifty_symbols)):
        raise ValueError("Nifty 500 source contains duplicate symbols")
    if len(fno_symbols) != len(set(fno_symbols)):
        raise ValueError("F&O symbol set contains duplicates")

    active_aliases = {}
    for alias in aliases:
        old_symbol = alias.get("old_symbol")
        new_symbol = alias.get("new_symbol")
        effective = date.fromisoformat(alias["effective_date"])
        if not old_symbol or not new_symbol or old_symbol == new_symbol:
            raise ValueError(f"invalid symbol alias: {alias!r}")
        if effective <= fno_snapshot_date:
            if old_symbol in active_aliases and active_aliases[old_symbol] != new_symbol:
                raise ValueError(f"conflicting symbol aliases for {old_symbol}")
            active_aliases[old_symbol] = new_symbol

    rows = []
    for source in nifty_rows:
        symbol = source["symbol"].strip()
        matched_symbol = active_aliases.get(symbol, symbol)
        alias_used = matched_symbol != symbol
        eligible = matched_symbol in fno_symbols
        rows.append({
            "symbol": symbol,
            "company_name": source.get("company_name", ""),
            "industry": source.get("industry", ""),
            "series": source.get("series", ""),
            "isin": source.get("isin", ""),
            "fno_status": "eligible" if eligible else "not_eligible",
            "fno_evidence": (
                "official_current_list_symbol_alias_match"
                if eligible and alias_used
                else "present_in_official_current_stock_fno_underlying_list"
                if eligible
                else "not_present_in_official_current_stock_fno_underlying_list"
            ),
            "matched_fno_symbol": matched_symbol if eligible else "",
            "symbol_alias_applied": str(alias_used).lower(),
        })
    summary = {
        "nifty500_count": len(rows),
        "eligible_count": sum(row["fno_status"] == "eligible" for row in rows),
        "not_eligible_count": sum(row["fno_status"] == "not_eligible" for row in rows),
        "unknown_count": 0,
        "alias_match_count": sum(row["symbol_alias_applied"] == "true" for row in rows),
    }
    if summary["eligible_count"] + summary["not_eligible_count"] != summary["nifty500_count"]:
        raise ValueError("Nifty 500 F&O classification is not exhaustive")
    return rows, summary


def _csv_bytes(rows: list[dict]) -> bytes:
    columns = [
        "symbol", "company_name", "industry", "series", "isin",
        "fno_status", "fno_evidence", "matched_fno_symbol", "symbol_alias_applied",
    ]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def ingest(root: Path = Path(".")) -> dict:
    nifty_path = root / "data/raw/universe/nifty500_current.csv"
    fno_path = root / "data/raw/fno_underlyings/fno_underlyings_current.csv"
    reconciliation_path = root / "data/raw/fno_underlyings/fno_tracker_reconciliation_current.json"
    aliases_path = root / "config/symbol_aliases.json"

    nifty_bytes, nifty_rows = _read_csv(
        nifty_path, {"symbol", "company_name", "industry", "series", "isin"}
    )
    fno_bytes, fno_rows = _read_csv(
        fno_path, {"segment", "serial_number", "symbol", "underlying"}
    )
    nifty_hash = hashlib.sha256(nifty_bytes).hexdigest()
    fno_hash = hashlib.sha256(fno_bytes).hexdigest()
    nifty_manifest_path, nifty_manifest = _find_manifest(
        root / "data/snapshots/universe", nifty_hash
    )
    fno_manifest_path, fno_manifest = _find_manifest(
        root / "data/snapshots/fno_underlyings", fno_hash
    )
    fno_current_manifest_path = root / "data/raw/fno_underlyings/fno_underlyings_current_manifest.json"
    if not fno_current_manifest_path.is_file():
        raise FileNotFoundError("current F&O retrieval manifest is required")
    fno_current_manifest = json.loads(fno_current_manifest_path.read_text(encoding="utf-8"))
    if fno_current_manifest.get("normalized_sha256") != fno_hash:
        raise ValueError("current F&O retrieval manifest does not match current normalized CSV")
    if not reconciliation_path.is_file():
        raise FileNotFoundError("effective tracker reconciliation status is required")
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    if reconciliation.get("status") != "pass" or reconciliation.get("mismatch_count") != 0:
        raise ValueError("current F&O list has not passed tracker-history reconciliation")

    aliases = json.loads(aliases_path.read_text(encoding="utf-8")).get("aliases", [])
    fno_symbols = {
        row["symbol"] for row in fno_rows if row["segment"] == "individual_stock"
    }
    fno_retrieved = datetime.fromisoformat(
        fno_current_manifest["retrieved_at_utc"].replace("Z", "+00:00")
    ).astimezone(ZoneInfo("Asia/Kolkata"))
    rows, summary = build_status_rows(
        nifty_rows, fno_symbols, aliases, fno_retrieved.date()
    )
    for row in rows:
        row["fno_snapshot_retrieved_at_utc"] = fno_manifest["retrieved_at_utc"]
        row["nifty500_snapshot_retrieved_at_utc"] = nifty_manifest["retrieved_at_utc"]
        row["nifty500_source_freshness_statement"] = nifty_manifest.get(
            "source_freshness_statement", "not supplied"
        )
    columns = [
        "symbol", "company_name", "industry", "series", "isin",
        "fno_status", "fno_evidence", "matched_fno_symbol", "symbol_alias_applied",
        "fno_snapshot_retrieved_at_utc", "nifty500_snapshot_retrieved_at_utc",
        "nifty500_source_freshness_statement",
    ]
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    output = stream.getvalue().encode("utf-8")
    output_hash = hashlib.sha256(output).hexdigest()

    raw_dir = root / "data/raw/fno_universe"
    snapshot_dir = root / "data/snapshots/fno_universe"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    current_path = raw_dir / "nifty500_fno_status_current.csv"
    snapshot_path = snapshot_dir / f"nifty500_fno_status_{output_hash[:16]}.csv"
    manifest_path = snapshot_dir / f"nifty500_fno_status_{output_hash[:16]}.json"
    for path in (snapshot_path,):
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != output_hash:
            raise RuntimeError(f"immutable F&O status snapshot path collision: {path}")
    if not snapshot_path.exists():
        snapshot_path.write_bytes(output)
    current_path.write_bytes(output)

    manifest = {
        "dataset": "nifty500_current_stock_fno_status",
        "publisher": "NSE Indices + National Stock Exchange of India",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "nifty500_source_manifest": str(nifty_manifest_path),
        "nifty500_source_normalized_sha256": nifty_hash,
        "nifty500_source_freshness_statement": nifty_manifest.get("source_freshness_statement"),
        "nifty500_source_effective_date": None,
        "fno_underlying_source_manifest": str(fno_manifest_path),
        "fno_underlying_normalized_sha256": fno_hash,
        "fno_current_retrieval_manifest": str(fno_current_manifest_path),
        "fno_membership_retrieved_at_utc": fno_current_manifest["retrieved_at_utc"],
        "fno_membership_effective_date": None,
        "tracker_reconciliation_status": reconciliation["status"],
        "tracker_reconciliation_mismatch_count": reconciliation["mismatch_count"],
        "symbol_aliases_sha256": hashlib.sha256(aliases_path.read_bytes()).hexdigest(),
        "output_sha256": output_hash,
        "counts": summary,
        "status_semantics": {
            "eligible": "symbol is present in NSE's current individual-stock F&O underlying list",
            "not_eligible": "symbol is absent from NSE's current individual-stock F&O underlying list",
            "unknown": "reserved for missing or unvalidated source data; none in this validated snapshot",
        },
    }
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "counts": summary,
        "current_path": str(current_path),
        "snapshot_path": str(snapshot_path),
        "manifest_path": str(manifest_path),
        "output_sha256": output_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(ingest(args.repo_root), indent=2))


if __name__ == "__main__":
    main()
