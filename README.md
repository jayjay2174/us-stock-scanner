# NYSE/Nasdaq Strong-Stock Scanner

A research-only Python scanner that looks for strong or sharply rising US-listed stocks. It does **not** place orders or connect to a brokerage.

The default hard filters are:

- Exchange: NYSE or Nasdaq
- Latest adjusted close: greater than `$2`
- Verified market capitalization: greater than `$300m`
- Final result: Top 20 idea candidates, combining price trend, momentum, volume and recent headline context

## What it produces

Each run writes:

- `reports/latest.csv` — machine-readable ranked candidates
- `reports/latest.md` — readable scan funnel, ranking, news context and caveats
- `reports/archive/YYYY-MM-DD.csv` and `.md` — dated snapshots

Demo mode uses the `-demo` suffix so synthetic data can never be mistaken for a live scan.

## Quick start

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scanner.py --demo
```

The demo is deterministic and needs no network access. For a small live smoke test:

```bash
python scanner.py --symbols AAPL,MSFT,NVDA,AMD,META,PLTR --top 5
```

For the complete NYSE/Nasdaq run:

```bash
python scanner.py --top 20
```

Useful options:

```text
--min-price 2
--min-market-cap 300000000
--no-news
--history-chunk-size 150
--max-market-cap-lookups 600
--output-dir reports
```

Run tests with `pytest -q`.

## Ranking method

The scanner first gets at least 22 trading sessions of adjusted close and volume data. It calculates:

- 1-day, 5-day and 20-day return
- Current volume divided by 20-day average volume
- Price versus 20-day moving average
- 20-day versus 50-day moving-average trend
- Distance from the 20-day high
- 14-session RSI

The price/momentum score uses robust cross-sectional z-scores. Its weights are 25% 1-day return, 25% 5-day return, 20% 20-day return, 15% relative volume, 10% price versus the 20-day average and 5% RSI. The final score is 85% price/momentum and 15% simple headline-keyword tone.

This is a candidate-prioritization rule, not a prediction model. A high rank means “research next,” not “buy.”

## Data sources and replaceable interfaces

No paid API key is required by default:

- **Universe:** official Nasdaq Trader `nasdaqlisted.txt` and `otherlisted.txt` symbol directories. ETFs, test issues and obvious warrants/rights/units/preferreds are removed; the other-exchange file is restricted to NYSE (`Exchange=N`).
- **Price, volume and market cap:** Yahoo Finance through `yfinance`.
- **News:** Yahoo Finance search results through `yfinance`, fetched only for the leading eligible candidates.

The interfaces in `stock_scanner/providers.py` deliberately separate `UniverseProvider`, `MarketDataProvider` and `NewsProvider`. A Polygon, Alpaca, Tiingo, Finnhub, paid terminal export or internal database can replace one component without changing scoring or reporting. Implement the matching protocol and inject it in `scanner.py`.

### Free-source limitations

`yfinance` is an open-source wrapper around Yahoo's public endpoints; it is not an official exchange feed. Data can be delayed, rate-limited, incomplete, or changed without notice, and Yahoo data is generally intended for personal research use. The scanner therefore:

- downloads history in chunks;
- uses split-adjusted daily prices and keeps the `yfinance` cache inside the project;
- limits the more expensive market-cap checks to the strongest preliminary candidates;
- excludes unknown market caps instead of bypassing the `$300m` filter;
- reports coverage and missing-data warnings;
- marks the report `PARTIAL` when usable price coverage is below 80% or fewer than the requested candidates pass every gate;
- keeps a network-free demo path for tests.

With the default lookup ceiling, the scan prioritizes practicality on a free source. If too many preliminary candidates lack market-cap data, increase `--max-market-cap-lookups`, reduce concurrent pressure in the provider, or plug in a bulk fundamentals source. Confirm any candidate against current primary filings and a reliable market-data source.

## GitHub Actions

`.github/workflows/daily_scan.yml` runs Monday through Friday at 22:37 UTC, safely after the normal US close across daylight-saving changes. It can also be started manually from the Actions tab.

The workflow:

1. installs dependencies;
2. runs tests;
3. executes the full scan;
4. uploads `reports/` as a 30-day artifact;
5. attempts to commit refreshed reports back to the repository.

Repository settings must allow GitHub Actions read/write access for automatic report commits. Protected branches may reject the push; the uploaded artifact remains available even when that optional commit step fails. GitHub notes that scheduled workflows can be delayed under heavy load and only run from the default branch.

## Responsible use

This project is for screening and education. It is not investment advice, does not assess suitability, and does not account for spreads, halts, corporate actions, liquidity at your order size, taxes or portfolio risk. Review company filings, the original news, and current quotes before making decisions.
