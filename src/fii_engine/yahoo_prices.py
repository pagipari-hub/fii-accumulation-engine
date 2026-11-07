"""Yahoo Finance historical-price adapter; not an FII attribution source."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

OUTPUT_COLUMNS = (
    "date", "symbol", "open", "high", "low", "close",
    "adj_close", "volume", "dividends", "stock_splits",
)


def read_symbols(path: Path) -> list[str]:
    """Read symbols from a CSV with a 'symbol' header, or from its first column."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.reader(handle)
        try:
            header = next(rows)
        except StopIteration:
            return []
        normalized = [item.strip().lower() for item in header]
        if "symbol" not in normalized:
            raise ValueError("Symbols CSV must contain a 'symbol' column")
        column = normalized.index("symbol")
        symbols = []
        for row in rows:
            if len(row) > column and row[column].strip():
                symbol = row[column].strip().upper()
                if symbol not in symbols:
                    symbols.append(symbol)
    return symbols


def yahoo_ticker(symbol: str) -> str:
    """Map an NSE symbol to Yahoo Finance's .NS convention."""
    clean = symbol.strip().upper()
    if not clean or any(char.isspace() for char in clean):
        raise ValueError(f"Invalid NSE symbol: {symbol!r}")
    return clean if clean.endswith((".NS", ".BO")) else f"{clean}.NS"


def _number(value: object):
    if value is None:
        return None
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except ImportError:
        pass
    return value.item() if hasattr(value, "item") else value


def collect_prices(symbols: Iterable[str], *, period: str = "2y", interval: str = "1d"):
    """Download historical prices and return normalized rows and provenance metadata."""
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            'Install optional dependencies with: pip install -e ".[market-data]"'
        ) from exc

    requested = list(dict.fromkeys(yahoo_ticker(item) for item in symbols))
    if not requested:
        raise ValueError("No symbols supplied")
    frame = yf.download(
        tickers=requested, period=period, interval=interval,
        auto_adjust=False, actions=True, group_by="ticker",
        multi_level_index=True, threads=True, progress=False,
    )
    if frame.empty:
        raise RuntimeError("Yahoo Finance returned no price rows")

    rows = []
    for ticker in requested:
        if isinstance(frame.columns, pd.MultiIndex):
            if ticker not in frame.columns.get_level_values(0):
                continue
            part = frame[ticker].copy()
        elif len(requested) == 1:
            part = frame.copy()
        else:
            continue
        part = part.reset_index()
        date_column = "Date" if "Date" in part.columns else "Datetime"
        for record in part.to_dict(orient="records"):
            date_value = record.get(date_column)
            if pd.isna(date_value):
                continue
            rows.append({
                "date": date_value.isoformat() if hasattr(date_value, "isoformat") else str(date_value),
                "symbol": ticker.removesuffix(".NS").removesuffix(".BO"),
                "open": _number(record.get("Open")),
                "high": _number(record.get("High")),
                "low": _number(record.get("Low")),
                "close": _number(record.get("Close")),
                "adj_close": _number(record.get("Adj Close")),
                "volume": _number(record.get("Volume")),
                "dividends": _number(record.get("Dividends")),
                "stock_splits": _number(record.get("Stock Splits")),
            })
    if not rows:
        raise RuntimeError("Yahoo Finance returned no usable rows for requested symbols")

    metadata = {
        "provider": "Yahoo Finance via yfinance",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "period": period, "interval": interval, "auto_adjust": False,
        "requested_tickers": requested,
        "returned_symbols": sorted({row["symbol"] for row in rows}),
        "row_count": len(rows),
        "usage_note": "Unofficial interface; price context only, not FII attribution.",
    }
    return rows, metadata


def write_output(rows: list[dict], metadata: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "yahoo_prices.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    (output / "yahoo_prices_manifest.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/market-data/yahoo"))
    parser.add_argument("--period", default="2y")
    parser.add_argument("--interval", default="1d")
    args = parser.parse_args()
    rows, metadata = collect_prices(read_symbols(args.symbols_file),
                                    period=args.period, interval=args.interval)
    write_output(rows, metadata, args.output)
    print(f"Wrote {len(rows)} rows for {len(metadata['returned_symbols'])} symbols")


if __name__ == "__main__":
    main()
