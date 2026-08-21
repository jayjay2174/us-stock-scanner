"""Replaceable market-universe, price/metadata, and news providers."""

from __future__ import annotations

import io
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
import pandas as pd
import requests
import yfinance as yf

LOGGER = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str = ""
    publisher: str = ""
    published_utc: str = ""


class UniverseProvider(Protocol):
    def get_universe(self) -> pd.DataFrame:
        """Return symbol, name, and exchange columns."""


class MarketDataProvider(Protocol):
    def get_history(
        self, symbols: Sequence[str], period: str, chunk_size: int
    ) -> dict[str, pd.DataFrame]:
        """Return daily Close and Volume frames keyed by scanner symbol."""

    def get_market_caps(self, symbols: Sequence[str]) -> dict[str, float | None]:
        """Return market caps in USD when available."""


class NewsProvider(Protocol):
    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        """Return recent headlines for one symbol."""


def _http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; research-stock-scanner/0.1; "
                "+https://github.com/)"
            )
        }
    )
    return session


def _read_pipe_table(text: str) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO(text), sep="|")
    first_column = frame.columns[0]
    frame = frame[~frame[first_column].astype(str).str.startswith("File Creation Time")]
    frame = frame.dropna(how="all")
    return frame


def _looks_like_common_equity(name: str) -> bool:
    value = f" {name.upper()} "
    excluded = (
        " WARRANT",
        " WT EXP",
        " RIGHT",
        " UNIT",
        " PREFERRED",
        " PFD",
        " NOTE DUE",
        " BOND",
        " DEBENTURE",
    )
    return not any(token in value for token in excluded)


def to_yahoo_symbol(symbol: str) -> str:
    """Translate common US share-class punctuation to Yahoo's convention."""
    return symbol.replace(".", "-").replace("/", "-")


class NasdaqTraderUniverseProvider:
    """Official Nasdaq Trader symbol directories; excludes ETFs and non-NYSE venues."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def get_universe(self) -> pd.DataFrame:
        session = _http_session()
        nasdaq_response = session.get(NASDAQ_LISTED_URL, timeout=self.timeout)
        nasdaq_response.raise_for_status()
        other_response = session.get(OTHER_LISTED_URL, timeout=self.timeout)
        other_response.raise_for_status()

        nasdaq = _read_pipe_table(nasdaq_response.text)
        nasdaq = nasdaq[
            (nasdaq["Test Issue"] == "N")
            & (nasdaq["ETF"] == "N")
            & (nasdaq["Financial Status"] == "N")
            & nasdaq["Security Name"].map(_looks_like_common_equity)
        ]
        nasdaq_out = pd.DataFrame(
            {
                "symbol": nasdaq["Symbol"].astype(str),
                "name": nasdaq["Security Name"].astype(str),
                "exchange": "NASDAQ",
            }
        )

        other = _read_pipe_table(other_response.text)
        other = other[
            (other["Exchange"] == "N")
            & (other["Test Issue"] == "N")
            & (other["ETF"] == "N")
            & ~other["ACT Symbol"].astype(str).str.contains("$", regex=False)
            & other["Security Name"].map(_looks_like_common_equity)
        ]
        other_out = pd.DataFrame(
            {
                "symbol": other["ACT Symbol"].astype(str),
                "name": other["Security Name"].astype(str),
                "exchange": "NYSE",
            }
        )

        result = pd.concat([nasdaq_out, other_out], ignore_index=True)
        result = result.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)
        LOGGER.info("Loaded %d NYSE/Nasdaq common-equity symbols", len(result))
        return result


class YahooFinanceMarketDataProvider:
    """Unofficial Yahoo Finance access through yfinance; no API key required."""

    def __init__(self, cap_workers: int = 6, pause_between_chunks: float = 0.4) -> None:
        self.cap_workers = cap_workers
        self.pause_between_chunks = pause_between_chunks
        cache_dir = Path(os.environ.get("YFINANCE_CACHE_DIR", ".cache/yfinance"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir.resolve()))

    def get_history(
        self, symbols: Sequence[str], period: str, chunk_size: int
    ) -> dict[str, pd.DataFrame]:
        histories: dict[str, pd.DataFrame] = {}
        yahoo_to_source = {to_yahoo_symbol(symbol): symbol for symbol in symbols}
        yahoo_symbols = list(yahoo_to_source)

        for start in range(0, len(yahoo_symbols), chunk_size):
            chunk = yahoo_symbols[start : start + chunk_size]
            LOGGER.info(
                "Downloading price chunk %d-%d of %d",
                start + 1,
                min(start + len(chunk), len(yahoo_symbols)),
                len(yahoo_symbols),
            )
            try:
                data = yf.download(
                    chunk,
                    period=period,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    repair=False,
                    threads=True,
                    progress=False,
                    timeout=25,
                    multi_level_index=True,
                )
            except Exception as exc:
                LOGGER.warning("Price chunk failed and will be skipped: %s", exc)
                continue

            if data is None or data.empty:
                continue

            if isinstance(data.columns, pd.MultiIndex):
                available = set(data.columns.get_level_values(0))
                for yahoo_symbol in chunk:
                    if yahoo_symbol not in available:
                        continue
                    frame = data[yahoo_symbol]
                    if {"Close", "Volume"}.issubset(frame.columns):
                        clean = frame[["Close", "Volume"]].dropna(subset=["Close"])
                        if not clean.empty:
                            histories[yahoo_to_source[yahoo_symbol]] = clean
            elif len(chunk) == 1 and {"Close", "Volume"}.issubset(data.columns):
                clean = data[["Close", "Volume"]].dropna(subset=["Close"])
                if not clean.empty:
                    histories[yahoo_to_source[chunk[0]]] = clean

            if start + chunk_size < len(yahoo_symbols):
                time.sleep(self.pause_between_chunks)

        # Shared runners occasionally lose an individual symbol inside an otherwise
        # successful threaded batch. Retry a bounded number sequentially.
        missing = [symbol for symbol in yahoo_symbols if yahoo_to_source[symbol] not in histories]
        for yahoo_symbol in missing[:100]:
            try:
                data = yf.download(
                    [yahoo_symbol],
                    period=period,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    repair=False,
                    threads=False,
                    progress=False,
                    timeout=25,
                    multi_level_index=True,
                )
                if data is None or data.empty:
                    continue
                frame = data[yahoo_symbol] if isinstance(data.columns, pd.MultiIndex) else data
                if {"Close", "Volume"}.issubset(frame.columns):
                    clean = frame[["Close", "Volume"]].dropna(subset=["Close"])
                    if not clean.empty:
                        histories[yahoo_to_source[yahoo_symbol]] = clean
            except Exception as exc:
                LOGGER.debug("Sequential retry failed for %s: %s", yahoo_symbol, exc)

        LOGGER.info("Received usable history for %d/%d symbols", len(histories), len(symbols))
        return histories

    @staticmethod
    def _one_market_cap(symbol: str) -> float | None:
        yahoo_symbol = to_yahoo_symbol(symbol)
        try:
            fast_info = yf.Ticker(yahoo_symbol).fast_info
            for key in ("market_cap", "marketCap"):
                try:
                    value = getattr(fast_info, key, None)
                    if value is None and hasattr(fast_info, "get"):
                        value = fast_info.get(key)
                    if value is not None and math.isfinite(float(value)):
                        return float(value)
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception as exc:
            LOGGER.debug("Market cap unavailable for %s: %s", symbol, exc)
        return None

    def get_market_caps(self, symbols: Sequence[str]) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        with ThreadPoolExecutor(max_workers=self.cap_workers) as pool:
            futures = {pool.submit(self._one_market_cap, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result[symbol] = future.result()
                except Exception as exc:
                    LOGGER.debug("Market-cap worker failed for %s: %s", symbol, exc)
                    result[symbol] = None
        return result


class YahooFinanceNewsProvider:
    """Recent Yahoo Finance headlines through yfinance Search."""

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        try:
            raw_items = yf.Search(
                to_yahoo_symbol(symbol),
                max_results=1,
                news_count=limit,
                lists_count=0,
                include_cb=False,
                include_nav_links=False,
                include_research=False,
                recommended=0,
                timeout=15,
                raise_errors=False,
            ).news
        except Exception as exc:
            LOGGER.debug("News unavailable for %s: %s", symbol, exc)
            return []

        items: list[NewsItem] = []
        for raw in raw_items or []:
            content = raw.get("content", raw)
            title = str(content.get("title") or raw.get("title") or "").strip()
            if not title:
                continue
            canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
            url = canonical.get("url", "") if isinstance(canonical, dict) else ""
            url = str(url or raw.get("link") or "")
            provider = content.get("provider") or {}
            publisher = (
                provider.get("displayName", "") if isinstance(provider, dict) else str(provider)
            )
            published = str(content.get("pubDate") or "")
            if not published and raw.get("providerPublishTime"):
                published = datetime.fromtimestamp(
                    int(raw["providerPublishTime"]), tz=timezone.utc
                ).isoformat()
            items.append(NewsItem(title, url, publisher, published))
        return items[:limit]


class DemoUniverseProvider:
    def get_universe(self) -> pd.DataFrame:
        rows = []
        for index in range(36):
            rows.append(
                {
                    "symbol": f"DEMO{index + 1:02d}",
                    "name": f"Synthetic Company {index + 1:02d}",
                    "exchange": "NASDAQ" if index % 2 == 0 else "NYSE",
                }
            )
        return pd.DataFrame(rows)


class DemoMarketDataProvider:
    def get_history(
        self, symbols: Sequence[str], period: str, chunk_size: int
    ) -> dict[str, pd.DataFrame]:
        dates = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=70)
        result: dict[str, pd.DataFrame] = {}
        for index, symbol in enumerate(symbols):
            rng = np.random.default_rng(10_000 + index)
            drift = 0.0005 + index * 0.00012
            returns = rng.normal(drift, 0.012 + (index % 4) * 0.0015, len(dates))
            close = (4.0 + index * 2.7) * np.cumprod(1 + returns)
            close[-1] *= 1 + (index % 9) * 0.012
            volume = rng.integers(200_000, 3_000_000, len(dates)).astype(float)
            volume[-1] *= 1 + (index % 6) * 0.4
            result[symbol] = pd.DataFrame({"Close": close, "Volume": volume}, index=dates)
        return result

    def get_market_caps(self, symbols: Sequence[str]) -> dict[str, float | None]:
        return {
            symbol: 180_000_000 + (int(symbol[-2:]) * 145_000_000) for symbol in symbols
        }


class DemoNewsProvider:
    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        number = int(symbol[-2:])
        adjective = "raises outlook after strong growth" if number % 3 else "faces analyst downgrade"
        timestamp = (datetime.now(timezone.utc) - timedelta(hours=number)).isoformat()
        return [
            NewsItem(
                title=f"{symbol} {adjective}",
                url=f"https://example.invalid/{symbol.lower()}",
                publisher="Synthetic News",
                published_utc=timestamp,
            )
        ][:limit]
