# Market-data provider strategy

## Execution model

GitHub Actions is the primary scheduled runner. The same Python package and
command-line interfaces run locally on a PC as a recovery/backfill path.

## Yahoo Finance price adapter

Install the optional dependencies:

```bash
python -m pip install -e ".[market-data]"
```

Run locally or in GitHub Actions:

```bash
python -m fii_engine.yahoo_prices --symbols-file data/raw/universe/nifty500_current.csv --period 2y
```

The input CSV must contain a `symbol` column. The adapter maps NSE symbols to
Yahoo's `.NS` convention and writes unadjusted OHLC, adjusted close, volume,
dividends, splits, plus a retrieval manifest.

### Data boundaries

- Yahoo Finance through yfinance is an unofficial interface.
- Use it for price/volume context, never as evidence of FII/FPI transactions.
- `auto_adjust=False` is explicit; raw OHLC and adjusted close remain separate.
- Preserve provider, retrieval time, requested tickers, period, interval and row count.
- Empty or unusable responses are failures, not zero-activity observations.
- Check data completeness and applicable usage rights before operational reliance.

## NSE adapter evaluation

`jugaad-data` remains a candidate, not a production dependency. Validate package
maintenance, Python compatibility, exact report coverage, schema stability and
GitHub Actions reachability before enabling it. It cannot remove NSE access
restrictions.

## Provider-neutral rule

Provider-specific downloads must normalize into common engine schemas. Keep
provider identity and retrieval provenance attached to each dataset. A provider
outage must be explicitly unavailable, never silently converted to zero.
