"""Extract category-level ownership facts from official NSE shareholding XBRL.

The parser preserves reported fractions and derives percentage points explicitly.
It does not infer missing categories as zero and does not treat ownership changes
as dated transactions.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


CATEGORY_AXIS = "CategoryOfShareholdersAxis"
FACT_FIELDS = {
    "NumberOfShareholders": "shareholder_count",
    "NumberOfShares": "shares",
    "NumberOfFullyPaidUpEquityShares": "fully_paid_shares",
    "ShareholdingAsAPercentageOfTotalNumberOfShares": "ownership_fraction",
    "NumberOfVotingRights": "voting_rights",
    "PercentageOfTotalVotingRights": "voting_rights_fraction",
}
FPI_MEMBERS = {
    "InstitutionsForeignPortfolioInvestorCategoryOne": "fpi_category_i",
    "InstitutionsForeignPortfolioInvestorCategoryTwo": "fpi_category_ii",
}
FOREIGN_AGGREGATE_MEMBERS = {
    "InstitutionsForeign": "foreign_institutions_total",
    "OtherInstitutionsForeign": "other_foreign_institutions",
    "OverseasDepositories": "overseas_depositories",
    "ForeignDirectInvestment": "foreign_direct_investment",
    "ForeignVentureCapitalInvestors": "foreign_venture_capital_investors",
    "SovereignWealthFundsForeign": "sovereign_wealth_funds_foreign",
}


def _local_name(value: str) -> str:
    """Return the local part of an XML QName or prefixed QName."""
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _decimal(value: str, *, field: str, context_id: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(
            f"non-numeric XBRL value for {field} in context {context_id}: {value!r}"
        ) from exc
    if not result.is_finite():
        raise ValueError(f"non-finite XBRL value for {field} in context {context_id}")
    return result


def parse_xbrl(payload: bytes) -> dict:
    """Parse all shareholder-category contexts and reconcile FPI category totals."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("NSE shareholding filing is not valid XML") from exc

    contexts: dict[str, dict] = {}
    for element in root.iter():
        if _local_name(element.tag).lower() != "context":
            continue
        context_id = element.attrib.get("id")
        if not context_id:
            raise ValueError("XBRL context is missing its id")
        period = {}
        category_members = []
        dimensions = []
        entity_identifier = None
        for child in element.iter():
            local = _local_name(child.tag).lower()
            if local == "identifier":
                entity_identifier = (child.text or "").strip()
            elif local in {"instant", "startdate", "enddate", "forever"}:
                period[local] = (child.text or "").strip()
            elif local in {"explicitmember", "typedmember"}:
                dimension = child.attrib.get("dimension", "")
                member_value = (child.text or "").strip()
                dimensions.append({
                    "dimension": _local_name(dimension),
                    "member": _local_name(member_value),
                })
                if local == "explicitmember" and _local_name(dimension) == CATEGORY_AXIS:
                    member = _local_name(member_value)
                    if member.endswith("Member"):
                        member = member[:-6]
                    if member:
                        category_members.append(member)
        if len(category_members) > 1:
            raise ValueError(
                f"multiple shareholder-category dimensions in context {context_id}"
            )
        if category_members:
            instant = period.get("instant")
            if not instant:
                raise ValueError(
                    f"shareholder category context {context_id} has no instant period"
                )
            try:
                date.fromisoformat(instant)
            except ValueError as exc:
                raise ValueError(
                    f"invalid shareholder reporting date in context {context_id}: {instant!r}"
                ) from exc
        contexts[context_id] = {
            "period": period,
            "category_member": category_members[0] if category_members else None,
            "entity_identifier": entity_identifier,
            "dimensions": dimensions,
        }

    facts: dict[str, dict[str, tuple[Decimal, str | None]]] = defaultdict(dict)
    for element in root.iter():
        context_id = element.attrib.get("contextRef")
        field = _local_name(element.tag)
        if context_id not in contexts or field not in FACT_FIELDS:
            continue
        nil = element.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil", "false")
        if nil.lower() == "true":
            continue
        raw_value = (element.text or "").strip()
        if not raw_value:
            continue
        value = _decimal(raw_value, field=field, context_id=context_id)
        unit_ref = element.attrib.get("unitRef")
        previous = facts[context_id].get(field)
        current = (value, unit_ref)
        if previous is not None and previous != current:
            raise ValueError(
                f"conflicting duplicate XBRL fact {field} in context {context_id}"
            )
        facts[context_id][field] = current

    category_records = []
    for context_id, context in contexts.items():
        member = context["category_member"]
        if not member:
            continue
        record = {
            "context_id": context_id,
            "reporting_date": context["period"].get("instant"),
            "category_member": member,
            "category_key": FPI_MEMBERS.get(
                member, FOREIGN_AGGREGATE_MEMBERS.get(member, member)
            ),
            "entity_identifier": context["entity_identifier"],
            "dimensions": context["dimensions"],
        }
        for fact_name, output_name in FACT_FIELDS.items():
            item = facts.get(context_id, {}).get(fact_name)
            if item is None:
                record[output_name] = None
                continue
            value, unit_ref = item
            if output_name in {"shares", "fully_paid_shares", "shareholder_count", "voting_rights"}:
                if value < 0:
                    raise ValueError(f"negative count in {fact_name}, context {context_id}")
                if value != value.to_integral_value():
                    raise ValueError(
                        f"fractional count in {fact_name}, context {context_id}"
                    )
                record[output_name] = int(value)
            elif output_name in {"ownership_fraction", "voting_rights_fraction"}:
                if value < 0 or value > 1:
                    raise ValueError(
                        f"percentage fraction outside [0, 1] in {fact_name}, context {context_id}"
                    )
                record[output_name] = str(value)
                record[output_name.replace("_fraction", "_percentage_points")] = str(
                    value * Decimal(100)
                )
            record[f"{output_name}_unit_ref"] = unit_ref
        category_records.append(record)

    if not category_records:
        raise ValueError("XBRL filing contains no shareholder CategoryOfShareholdersAxis contexts")

    by_date_member = {}
    for row in category_records:
        key = (row["reporting_date"], row["category_member"])
        if key in by_date_member:
            previous = by_date_member[key]
            comparable = (
                "shareholder_count", "shares", "fully_paid_shares",
                "ownership_fraction", "voting_rights", "voting_rights_fraction",
            )
            if any(previous.get(field) != row.get(field) for field in comparable):
                raise ValueError(
                    "multiple contexts for shareholder category "
                    f"{key[1]} on {key[0]}; refusing to overwrite a reported category"
                )
            raise ValueError(
                "multiple contexts for shareholder category "
                f"{key[1]} on {key[0]}; category aggregation is not inferred"
            )
        by_date_member[key] = row
    reconciliation = []
    dates = sorted({row["reporting_date"] for row in category_records if row["reporting_date"]})
    for reporting_date in dates:
        def shares(member: str):
            row = by_date_member.get((reporting_date, member))
            return None if row is None else row["shares"]

        category_i = shares("InstitutionsForeignPortfolioInvestorCategoryOne")
        category_ii = shares("InstitutionsForeignPortfolioInvestorCategoryTwo")
        other_foreign = shares("OtherInstitutionsForeign")
        optional_members = {
            "overseas_depositories": "OverseasDepositories",
            "foreign_direct_investment": "ForeignDirectInvestment",
            "foreign_venture_capital_investors": "ForeignVentureCapitalInvestors",
            "sovereign_wealth_funds_foreign": "SovereignWealthFundsForeign",
        }
        optional_rows = {
            key: by_date_member.get((reporting_date, member))
            for key, member in optional_members.items()
        }
        optional_values = {
            key: None if row is None else row["shares"]
            for key, row in optional_rows.items()
        }
        foreign_total = shares("InstitutionsForeign")
        base_available = all(
            value is not None
            for value in (category_i, category_ii, other_foreign, foreign_total)
        )
        # Include each additional foreign-institution component only when the issuer
        # explicitly reports it. An absent context is not converted to zero; a present
        # nil fact remains unavailable.
        optional_available = all(
            row is None or optional_values[key] is not None
            for key, row in optional_rows.items()
        )
        available = base_available and optional_available
        optional_sum = sum(
            optional_values[key]
            for key, row in optional_rows.items()
            if row is not None
        ) if available else None
        component_sum = (
            category_i + category_ii + other_foreign + optional_sum
            if available else None
        )
        reconciliation.append({
            "reporting_date": reporting_date,
            "fpi_category_i_shares": category_i,
            "fpi_category_ii_shares": category_ii,
            "other_foreign_institution_shares": other_foreign,
            "overseas_depositories_shares": optional_values["overseas_depositories"],
            "foreign_direct_investment_shares": optional_values["foreign_direct_investment"],
            "foreign_venture_capital_investor_shares": optional_values["foreign_venture_capital_investors"],
            "sovereign_wealth_funds_foreign_shares": optional_values["sovereign_wealth_funds_foreign"],
            "foreign_institutions_total_shares": foreign_total,
            "component_sum_shares": component_sum,
            "difference_shares": (
                component_sum - foreign_total if available else None
            ),
            "status": (
                "matched" if available and component_sum == foreign_total
                else "mismatch" if available else "not_comparable_missing_component"
            ),
            "scope_note": (
                "FPI Category I and II remain distinct from FDI, foreign venture capital, "
                "foreign sovereign wealth funds, other foreign institutions and overseas "
                "depositories. Additional categories are included only when explicitly "
                "reported; absent or nil components are not assumed to be zero."
            ),
        })

    return {
        "parser": "nse_shareholding_xbrl",
        "parser_version": 3,
        "category_axis": CATEGORY_AXIS,
        "reporting_dates": dates,
        "category_record_count": len(category_records),
        "category_records": category_records,
        "fpi_reconciliation": reconciliation,
        "semantics": {
            "ownership_fraction": "XBRL reported decimal fraction; multiply by 100 for percentage points",
            "shares": "Reported share count from the NumberOfShares fact",
            "missing": "Absent or nil facts remain null; they are never converted to zero",
            "attribution": "Quarterly reported ownership, not daily trade flow or purchase date",
        },
    }


def parse_xbrl_file(path: Path) -> dict:
    return parse_xbrl(path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = parse_xbrl_file(args.input)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
