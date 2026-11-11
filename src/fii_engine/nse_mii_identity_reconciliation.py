"""Compare quarter-end ownership mismatch candidates with the dated NSE CM MII master.

This is an exchange security-reference check only. It never changes ownership
identity status or releases FPI ownership evidence.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


def reconcile(mii_gzip: Path, candidates_csv: Path, output_dir: Path) -> dict:
    payload = mii_gzip.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    with gzip.open(mii_gzip, "rt", encoding="utf-8-sig", newline="") as handle:
        master_rows = list(csv.DictReader(handle))
    by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in master_rows:
        if row.get("SctySrs", "").strip().upper() == "EQ":
            by_symbol[row.get("TckrSymb", "").strip()].append(row)

    with candidates_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    if not candidates:
        raise ValueError("candidate audit CSV is empty")
    required = {
        "reporting_date", "symbol", "universe_isin", "master_isin",
        "master_company_name", "master_record_id",
    }
    missing = required - set(candidates[0])
    if missing:
        raise ValueError(f"candidate audit schema missing: {sorted(missing)}")
    dates = {row["reporting_date"] for row in candidates}
    if len(dates) != 1:
        raise ValueError(f"candidate audit has mixed reporting dates: {sorted(dates)}")
    reporting_date = date.fromisoformat(next(iter(dates)))
    output_rows = []
    counts: dict[str, int] = defaultdict(int)

    for candidate in candidates:
        symbol = candidate["symbol"].strip()
        matches = by_symbol.get(symbol, [])
        enriched = dict(candidate)
        enriched.update({
            "mii_match_status": "",
            "mii_fin_instrm_id": "",
            "mii_security_name": "",
            "mii_isin": "",
            "mii_series": "",
            "mii_del_flg": "",
            "mii_listg_dt_epoch": "",
            "mii_rmvl_dt_epoch": "",
            "mii_payload_sha256": digest,
            "identity_scope": (
                "Dated NSE CM MII symbol-to-security reference only; "
                "not proof that the shareholding XBRL entity owns this security."
            ),
        })
        if len(matches) == 0:
            status = "no_eq_symbol_match"
        elif len(matches) > 1:
            status = "ambiguous_eq_symbol_match"
        else:
            record = matches[0]
            isin = record.get("ISIN", "").strip().upper()
            enriched.update({
                "mii_fin_instrm_id": record.get("FinInstrmId", ""),
                "mii_security_name": record.get("FinInstrmNm", ""),
                "mii_isin": isin,
                "mii_series": record.get("SctySrs", ""),
                "mii_del_flg": record.get("DelFlg", ""),
                "mii_listg_dt_epoch": record.get("ListgDt", ""),
                "mii_rmvl_dt_epoch": record.get("RmvlDt", ""),
            })
            if record.get("DelFlg", "").strip().upper() == "Y":
                status = "eq_record_marked_deleted"
            elif isin == candidate["universe_isin"].strip().upper():
                status = "dated_mii_matches_universe_isin"
            else:
                status = "dated_mii_conflicts_with_universe_isin"
        enriched["mii_match_status"] = status
        counts[status] += 1
        output_rows.append(enriched)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "shareholding_mii_identity_reconciliation.csv"
    fields = list(output_rows[0])
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    report = {
        "reporting_date": reporting_date.isoformat(),
        "candidate_count": len(candidates),
        "mii_eq_record_count": sum(len(v) for v in by_symbol.values()),
        "mii_payload_sha256": digest,
        "mii_source_file": str(mii_gzip),
        "counts": dict(sorted(counts.items())),
        "identity_safety": {
            "no_ownership_identity_status_changed": True,
            "no_isin_override_performed": True,
            "mii_symbol_isin_match_is_not_xbrl_entity_ownership_proof": True,
            "unresolved_rows_remain_unverified": True,
        },
        "output_csv": str(output_csv),
    }
    (output_dir / "shareholding_mii_identity_reconciliation.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mii-gzip", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.mii_gzip, args.candidates, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
