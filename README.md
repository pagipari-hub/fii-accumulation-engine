# FII Accumulation Engine

An independent, evidence-driven research engine designed to identify and track potential Foreign Institutional Investor (FII/FPI) accumulation across the **full Nifty 500 universe**, including banking and financial stocks.

## Objective

Detect stocks showing potential institutional accumulation by combining multiple independent market-data sources rather than relying on a single indicator.

The engine produces two separate research horizons:

- **Short-term:** 1–4 weeks
- **Positional:** 1–6 months

## Research Framework

The engine integrates six independent research modules:

1. **Market Flows:** Daily FII/FPI and DII cash-market activity.
2. **Participant Positioning:** FII/FPI and other participant-level derivatives positioning.
3. **Stock Derivatives:** Futures OI, price-volume behaviour, options activity, PCR, IV, basis and MWPL.
4. **Cash Accumulation:** Delivery data, volume expansion, price strength and relative performance.
5. **Ownership Intelligence:** Shareholding changes, bulk deals and block deals.
6. **Sector Intelligence:** Sector rotation, relative strength and institutional activity patterns.

## Evidence Classification

Signals must be classified according to the strength of available evidence:

- **Grade 1 – Confirmed:** Direct, identifiable FII/FPI transaction evidence.
- **Grade 2 – Corroborated:** Multiple independent indicators support potential accumulation.
- **Grade 3 – Early Watchlist:** Preliminary signals requiring further confirmation.

Market-wide F&O positioning and stock-level derivatives activity must remain separate from confirmed stock-specific FII attribution.

## Scoring & Rankings

A transparent 100-point scoring framework will evaluate available evidence without double-counting correlated signals.

The engine will generate:

- Short-term accumulation rankings.
- Positional accumulation rankings.
- Overlapping opportunities.
- New, strengthening and weakening candidates.
- Historical ranking comparisons.
- Data-quality and missing-data reports.

Missing data will be marked as unavailable, never automatically treated as negative evidence.

## Universe

- Full Nifty 500 constituent universe.
- All sectors, including banks and financial institutions.
- F&O and cash-only stocks.
- Dynamic constituent updates with historical snapshots.

## Technology

Planned technology stack:

- Python, Pandas / Polars
- DuckDB and Parquet
- NSE data ingestion
- YAML-based configuration
- Streamlit dashboard
- GitHub Actions automation
- Pytest validation

## Development Roadmap

1. Build the Nifty 500 universe and data architecture.
2. Implement market-wide FII/DII flow ingestion.
3. Implement participant-level F&O data.
4. Build stock-level cash and derivatives analytics.
5. Integrate ownership and bulk/block deal intelligence.
6. Develop evidence fusion and scoring.
7. Generate daily and weekly research reports.
8. Build dashboard and automated execution.

## Core Principles

- Independent from other trading and stock-screening projects.
- Data-backed research, not assumptions.
- Preserve original source data and timestamps.
- Separate confirmed facts from inferred signals.
- Never confuse participant-level data with individual-stock FII activity.
- Record unavailable data transparently.
- Maintain reproducible historical reports.

## Disclaimer

This project is for quantitative research and educational purposes only. Its rankings and signals are research hypotheses, not investment recommendations or proof of institutional buying.
