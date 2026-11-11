"""Normalize official NSDL aggregate cash-flow archive HTML without double counting."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")


class TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._cell is not None:
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _rows(html: str) -> list[list[str]]:
    parser = TableRows()
    parser.feed(html)
    return parser.rows


def _number(value: str) -> float:
    value = value.strip().replace(",", "")
    negative = value.startswith("(") and value.endswith(")")
    result = float(value.strip("()"))
    return -result if negative else result


def _date_iso(value: str) -> str:
    return datetime.strptime(value, "%d-%b-%Y").date().isoformat()


def normalize_fpi_html(html: str, source_sha256: str, query_date: str) -> list[dict]:
    rows = _rows(html)
    header = next(
        (
            i
            for i, row in enumerate(rows)
            if row and row[0] == "Reporting Date"
            and any("Gross Purchases" in cell for cell in row)
        ),
        None,
    )
    if header is None:
        raise ValueError("FPI cash investment table header not found")
    output: dict[str, dict] = {}
    current_date: str | None = None
    equity_open = False
    for row in rows[header + 1 :]:
        if not row:
            continue
        first = row[0]
        if DATE_RE.match(first):
            current_date = _date_iso(first)
            equity_open = len(row) > 1 and row[1] == "Equity"
            continue
        if current_date is None or not equity_open:
            continue
        # Publisher's Equity Sub-total combines Stock Exchange and primary routes.
        if first.lower() == "sub-total" and len(row) >= 5:
            output[current_date] = {
                "date": current_date,
                "fpi_gross_purchases_inr_crore": _number(row[1]),
                "fpi_gross_sales_inr_crore": _number(row[2]),
                "fpi_net_investment_inr_crore": _number(row[3]),
                "fpi_source_query_date": query_date,
                "fpi_source_sha256": source_sha256,
            }
            equity_open = False
        elif first.startswith("Total for "):
            equity_open = False
    if not output:
        raise ValueError("FPI cash table had no dated Equity Sub-total rows")
    return list(output.values())


def normalize_dii_html(html: str, source_sha256: str, query_date: str) -> dict:
    rows = _rows(html)
    header = next(
        (i for i, row in enumerate(rows) if row and row[0] == "Type of DII"),
        None,
    )
    if header is None:
        raise ValueError("DII table header not found")
    # NSDL warns against summing investor/investment subtotals. Use its Equity Total.
    for row in rows[header + 1 :]:
        if row and row[0].strip().lower() == "equity total" and len(row) >= 5:
            return {
                "date": query_date,
                "dii_gross_purchases_inr_crore": _number(row[1]),
                "dii_gross_sales_inr_crore": _number(row[2]),
                "dii_net_investment_inr_crore": _number(row[3]),
                "dii_source_query_date": query_date,
                "dii_source_sha256": source_sha256,
            }
    raise ValueError(f"DII Equity Total row not found for {query_date}")


def normalize_manifest(manifest_path: Path, output_csv: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    start_date = manifest.get("start_date", "0001-01-01")
    end_date = manifest.get("end_date", "9999-12-31")
    combined: dict[str, dict] = {}
    fpi_count = 0
    dii_count = 0
    for observation in manifest["observations"]:
        if observation.get("http_status") != 200:
            raise ValueError("non-200 source response in manifest")
        raw_name = Path(observation["raw_path"]).name
        candidates = [
            root / observation["raw_path"],
            root / raw_name,
            root / observation["dataset"] / raw_name,
        ]
        raw = next((candidate for candidate in candidates if candidate.exists()), None)
        if raw is None:
            raise FileNotFoundError(
                f"raw response not found for {observation['query_date']}: {raw_name}"
            )
        payload = raw.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != observation["response_sha256"]:
            raise ValueError(f"raw hash mismatch: {raw}")
        html = payload.decode("utf-8", errors="replace")
        if observation["dataset"] == "fpi":
            normalized = normalize_fpi_html(html, digest, observation["query_date"])
            for item in normalized:
                day = item["date"]
                # Monthly NSDL archive pages may include dates before the requested window.
                if not start_date <= day <= end_date:
                    continue
                existing = combined.setdefault(day, {"date": day})
                for key, value in item.items():
                    if key in existing and existing[key] != value:
                        # Overlapping month responses may differ in provenance but not values.
                        if key.endswith(("_sha256", "_query_date")):
                            continue
                        raise ValueError(f"conflicting FPI duplicate for {day}: {key}")
                    existing[key] = value
            fpi_count += len(normalized)
        elif observation["dataset"] == "dii" and observation.get("response_type") == "data":
            item = normalize_dii_html(html, digest, observation["query_date"])
            day = item["date"]
            if not start_date <= day <= end_date:
                continue
            existing = combined.setdefault(day, {"date": day})
            for key, value in item.items():
                if key in existing and existing[key] != value:
                    raise ValueError(f"conflicting DII duplicate for {day}: {key}")
                existing[key] = value
            dii_count += 1
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date",
        "fpi_gross_purchases_inr_crore",
        "fpi_gross_sales_inr_crore",
        "fpi_net_investment_inr_crore",
        "fpi_source_query_date",
        "fpi_source_sha256",
        "dii_gross_purchases_inr_crore",
        "dii_gross_sales_inr_crore",
        "dii_net_investment_inr_crore",
        "dii_source_query_date",
        "dii_source_sha256",
    ]
    with output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for day in sorted(combined):
            writer.writerow(combined[day])
    return {
        "fpi_daily_rows_seen": fpi_count,
        "fpi_unique_dates": sum(
            "fpi_net_investment_inr_crore" in row for row in combined.values()
        ),
        "dii_daily_rows": dii_count,
        "combined_dates": len(combined),
        "output_csv": str(output_csv),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/nsdl_cash_flows.csv")
    )
    args = parser.parse_args()
    print(json.dumps(normalize_manifest(args.manifest, args.output), indent=2))


if __name__ == "__main__":
    main()
