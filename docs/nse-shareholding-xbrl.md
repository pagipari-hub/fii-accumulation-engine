# NSE shareholding XBRL extraction

## Verified filing semantics

The official NSE equity shareholding master links to XBRL filings. In the inspected
7 November 2026 capture, the BSE shareholding taxonomy represents shareholder
categories as explicit members of the `CategoryOfShareholdersAxis` dimension.

FPI ownership is represented by two distinct members:

- `InstitutionsForeignPortfolioInvestorCategoryOneMember`
- `InstitutionsForeignPortfolioInvestorCategoryTwoMember`

The filing also contains an `InstitutionsForeignMember` aggregate and an
`OtherInstitutionsForeignMember` category. In the inspected HDFCBANK filing,
Category I + Category II + Other Foreign Institutions reconciled exactly to
Institutions Foreign. Therefore the aggregate foreign-institutions value must
not be presented as FPI-only.

## Parser

`src/fii_engine/nse_shareholding_xbrl.py` extracts category-level facts by
joining each XBRL fact's `contextRef` to the context's explicit shareholder
category dimension. It extracts:

- Number of shares and fully paid-up shares
- Number of shareholders
- Reported ownership fraction and derived percentage points
- Voting rights and voting-rights percentage

The parser preserves the XBRL fraction (for example, `0.3762`) and separately
derives percentage points (`37.62`). It retains missing/nil facts as null,
rejects conflicting duplicate facts and records the reporting instant from
the context.

## Reconciliation and boundaries

Where all four components exist for a reporting date, the parser checks:

`FPI Category I + FPI Category II + Other Foreign Institutions = Institutions Foreign`

A mismatch is reported, not silently corrected. Missing components produce
`not_comparable_missing_component`, never zero. Category membership is
explicitly taxonomy-based; foreign nationals, NRIs and foreign companies are
not relabelled as FPI.

Quarterly ownership is reported holdings at a reporting date. It is not a daily
trade record, does not reveal the purchase date, and does not prove that a
specific FII bought shares during the quarter. The parser is a validated
extraction component; historical filing discovery, revision selection and
production ingestion remain separate work.
