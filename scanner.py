#!/usr/bin/env python3
"""CLI entry point for the US momentum stock scanner."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from stock_scanner.core import ScanConfig, run_scan
from stock_scanner.providers import (
    DemoMarketDataProvider,
    DemoNewsProvider,
    DemoUniverseProvider,
    NasdaqTraderUniverseProvider,
    YahooFinanceMarketDataProvider,
    YahooFinanceNewsProvider,
)
from stock_scanner.reporting import write_reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan NYSE/Nasdaq stocks for strong price and momentum signals."
    )
    parser.add_argument("--top", type=int, default=20, help="Number of final candidates.")
    parser.add_argument("--min-price", type=float, default=2.0)
    parser.add_argument("--min-market-cap", type=float, default=300_000_000)
    parser.add_argument("--period", default="3mo", help="yfinance history period.")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument(
        "--symbols",
        help="Optional comma-separated symbols for a quick smoke run instead of the full universe.",
    )
    parser.add_argument(
        "--no-news", action="store_true", help="Skip news retrieval and use a neutral news score."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run deterministic synthetic data through the complete pipeline without network access.",
    )
    parser.add_argument("--history-chunk-size", type=int, default=150)
    parser.add_argument("--market-cap-batch-size", type=int, default=50)
    parser.add_argument("--max-market-cap-lookups", type=int, default=600)
    parser.add_argument("--news-candidates", type=int, default=40)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.top <= 0:
        raise SystemExit("--top must be greater than zero")

    symbols = None
    if args.symbols:
        symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]

    config = ScanConfig(
        top_n=args.top,
        min_price=args.min_price,
        min_market_cap=args.min_market_cap,
        history_period=args.period,
        history_chunk_size=args.history_chunk_size,
        market_cap_batch_size=args.market_cap_batch_size,
        max_market_cap_lookups=args.max_market_cap_lookups,
        news_candidates=args.news_candidates,
        include_news=not args.no_news,
        symbols=symbols,
        data_mode="demo" if args.demo else "live",
    )

    if args.demo:
        universe_provider = DemoUniverseProvider()
        market_provider = DemoMarketDataProvider()
        news_provider = DemoNewsProvider() if config.include_news else None
    else:
        universe_provider = NasdaqTraderUniverseProvider()
        market_provider = YahooFinanceMarketDataProvider()
        news_provider = YahooFinanceNewsProvider() if config.include_news else None

    try:
        result = run_scan(config, universe_provider, market_provider, news_provider)
        paths = write_reports(result, args.output_dir)
    except Exception as exc:  # CLI boundary: log a concise actionable error.
        logging.exception("Scan failed: %s", exc)
        return 1

    logging.info("Wrote %s candidates to %s and %s", len(result.candidates), paths.csv, paths.markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
