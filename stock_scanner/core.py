"""Screen construction, filters, scoring, and candidate selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import numpy as np
import pandas as pd

from .providers import MarketDataProvider, NewsItem, NewsProvider, UniverseProvider

LOGGER = logging.getLogger(__name__)

POSITIVE_WORDS = {
    "approval",
    "beat",
    "beats",
    "breakout",
    "expands",
    "gain",
    "growth",
    "raises",
    "record",
    "strong",
    "surge",
    "upgrade",
    "wins",
}
NEGATIVE_WORDS = {
    "bankruptcy",
    "cuts",
    "downgrade",
    "drops",
    "falls",
    "fraud",
    "investigation",
    "lawsuit",
    "misses",
    "offering",
    "probe",
    "warning",
}


@dataclass(frozen=True)
class ScanConfig:
    top_n: int = 20
    min_price: float = 2.0
    min_market_cap: float = 300_000_000
    history_period: str = "3mo"
    history_chunk_size: int = 150
    market_cap_batch_size: int = 50
    max_market_cap_lookups: int = 600
    news_candidates: int = 40
    news_per_symbol: int = 5
    include_news: bool = True
    symbols: Sequence[str] | None = None
    data_mode: str = "live"


@dataclass
class ScanResult:
    candidates: pd.DataFrame
    generated_at_utc: datetime
    config: ScanConfig
    stats: dict[str, int | float | str]
    warnings: list[str] = field(default_factory=list)


def _finite(value: float | int | None) -> float:
    if value is None:
        return float("nan")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if np.isfinite(result) else float("nan")


def _rsi(close: pd.Series, periods: int = 14) -> float:
    if len(close) < periods + 1:
        return float("nan")
    delta = close.diff().dropna().tail(periods)
    gain = delta.clip(lower=0).mean()
    loss = -delta.clip(upper=0).mean()
    if loss == 0 and gain == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return float(100 - (100 / (1 + gain / loss)))


def _return(close: pd.Series, sessions: int) -> float:
    if len(close) < sessions + 1:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-(sessions + 1)] - 1)


def _robust_zscore(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    median = numeric.median()
    mad = (numeric - median).abs().median()
    if not np.isfinite(mad) or mad == 0:
        std = numeric.std(ddof=0)
        if not np.isfinite(std) or std == 0:
            return pd.Series(0.0, index=series.index)
        score = (numeric - numeric.mean()) / std
    else:
        score = 0.6745 * (numeric - median) / mad
    return score.fillna(0.0).clip(-3.0, 3.0)


def compute_price_metrics(
    universe: pd.DataFrame, histories: dict[str, pd.DataFrame], min_price: float
) -> pd.DataFrame:
    lookup = universe.set_index("symbol")
    rows: list[dict[str, object]] = []
    for symbol, history in histories.items():
        if symbol not in lookup.index or history.empty:
            continue
        clean = history[["Close", "Volume"]].copy()
        clean["Close"] = pd.to_numeric(clean["Close"], errors="coerce")
        clean["Volume"] = pd.to_numeric(clean["Volume"], errors="coerce")
        clean = clean.dropna(subset=["Close"])
        if len(clean) < 22:
            continue
        close = clean["Close"]
        volume = clean["Volume"]
        last_price = _finite(close.iloc[-1])
        if last_price <= min_price:
            continue
        avg_volume_20d = _finite(volume.tail(20).mean())
        volume_ratio = (
            _finite(volume.iloc[-1]) / avg_volume_20d if avg_volume_20d > 0 else float("nan")
        )
        sma20 = _finite(close.tail(20).mean())
        sma50 = _finite(close.tail(50).mean()) if len(close) >= 50 else float("nan")
        high20 = _finite(close.tail(20).max())
        record = lookup.loc[symbol]
        rows.append(
            {
                "symbol": symbol,
                "name": str(record["name"]),
                "exchange": str(record["exchange"]),
                "price": last_price,
                "return_1d": _return(close, 1),
                "return_5d": _return(close, 5),
                "return_20d": _return(close, 20),
                "volume_ratio_20d": volume_ratio,
                "price_vs_sma20": last_price / sma20 - 1 if sma20 > 0 else float("nan"),
                "sma20_vs_sma50": sma20 / sma50 - 1 if sma50 > 0 else float("nan"),
                "distance_from_20d_high": last_price / high20 - 1 if high20 > 0 else float("nan"),
                "rsi_14": _rsi(close),
                "price_as_of": str(clean.index[-1].date()),
            }
        )

    metrics = pd.DataFrame(rows)
    if metrics.empty:
        return metrics

    components = {
        "return_1d": 0.25,
        "return_5d": 0.25,
        "return_20d": 0.20,
        "volume_ratio_20d": 0.15,
        "price_vs_sma20": 0.10,
        "rsi_14": 0.05,
    }
    combined = pd.Series(0.0, index=metrics.index)
    for column, weight in components.items():
        combined += weight * _robust_zscore(metrics[column])
    metrics["price_momentum_score"] = (50 + 10 * combined).clip(0, 100)
    return metrics.sort_values("price_momentum_score", ascending=False).reset_index(drop=True)


def _headline_signal(items: list[NewsItem]) -> float:
    if not items:
        return 0.0
    scores = []
    for item in items:
        words = {word.strip(".,:;!?()[]{}'\"").lower() for word in item.title.split()}
        positive = len(words & POSITIVE_WORDS)
        negative = len(words & NEGATIVE_WORDS)
        scores.append(np.clip((positive - negative) / 2, -1, 1))
    return float(np.mean(scores))


def _enrich_news(frame: pd.DataFrame, provider: NewsProvider | None, limit: int) -> pd.DataFrame:
    result = frame.copy()
    news_signals: list[float] = []
    news_counts: list[int] = []
    headlines: list[str] = []
    urls: list[str] = []
    latest_dates: list[str] = []
    for symbol in result["symbol"]:
        items = provider.get_news(symbol, limit) if provider else []
        news_signals.append(_headline_signal(items))
        news_counts.append(len(items))
        headlines.append(" | ".join(item.title for item in items[:3]))
        urls.append(" | ".join(item.url for item in items[:3] if item.url))
        dates = [item.published_utc for item in items if item.published_utc]
        latest_dates.append(max(dates) if dates else "")
    result["news_signal"] = news_signals
    result["news_count"] = news_counts
    result["headlines"] = headlines
    result["news_urls"] = urls
    result["latest_news_utc"] = latest_dates
    result["news_score"] = 50 + 20 * result["news_signal"]
    result["combined_score"] = (
        0.85 * result["price_momentum_score"] + 0.15 * result["news_score"]
    ).clip(0, 100)
    return result


def _signal_label(row: pd.Series) -> str:
    if row["combined_score"] >= 65 and row["return_5d"] > 0 and row["price_vs_sma20"] > 0:
        return "Strong momentum"
    if row["combined_score"] >= 55:
        return "Positive momentum"
    return "Watchlist only"


def run_scan(
    config: ScanConfig,
    universe_provider: UniverseProvider,
    market_provider: MarketDataProvider,
    news_provider: NewsProvider | None,
) -> ScanResult:
    generated_at = datetime.now(timezone.utc)
    warnings: list[str] = []
    universe = universe_provider.get_universe()
    required = {"symbol", "name", "exchange"}
    if not required.issubset(universe.columns):
        raise ValueError(f"Universe provider must return columns: {sorted(required)}")
    if config.symbols:
        requested = {symbol.upper() for symbol in config.symbols}
        universe = universe[universe["symbol"].str.upper().isin(requested)].copy()
        missing = requested - set(universe["symbol"].str.upper())
        if missing:
            extras = pd.DataFrame(
                [{"symbol": symbol, "name": symbol, "exchange": "USER"} for symbol in missing]
            )
            universe = pd.concat([universe, extras], ignore_index=True)
    if universe.empty:
        raise RuntimeError("The selected universe is empty")

    histories = market_provider.get_history(
        universe["symbol"].tolist(), config.history_period, config.history_chunk_size
    )
    metrics = compute_price_metrics(universe, histories, config.min_price)
    if metrics.empty:
        raise RuntimeError("No symbols had sufficient price history after the price filter")

    target_eligible = max(config.top_n, config.news_candidates if config.include_news else config.top_n)
    caps: dict[str, float | None] = {}
    cursor = 0
    max_lookups = min(config.max_market_cap_lookups, len(metrics))
    eligible = pd.DataFrame()
    while cursor < max_lookups and len(eligible) < target_eligible:
        batch_end = min(cursor + config.market_cap_batch_size, max_lookups)
        batch = metrics.iloc[cursor:batch_end]["symbol"].tolist()
        caps.update(market_provider.get_market_caps(batch))
        cursor = batch_end
        enriched = metrics.iloc[:cursor].copy()
        enriched["market_cap"] = enriched["symbol"].map(caps)
        eligible = enriched[enriched["market_cap"].gt(config.min_market_cap)].copy()
        LOGGER.info(
            "Market-cap gate: %d eligible after %d lookups", len(eligible), cursor
        )

    unknown_caps = sum(value is None or not np.isfinite(_finite(value)) for value in caps.values())
    if unknown_caps:
        warnings.append(
            f"Excluded {unknown_caps} preselected symbols whose market cap could not be retrieved."
        )
    if cursor >= max_lookups and len(eligible) < target_eligible:
        warnings.append(
            "The market-cap lookup ceiling was reached before the preferred candidate pool was filled."
        )
    if eligible.empty:
        raise RuntimeError("No candidates passed the market-cap filter with a verified market cap")

    news_pool_size = min(len(eligible), max(config.top_n, config.news_candidates))
    candidates = _enrich_news(
        eligible.head(news_pool_size), news_provider if config.include_news else None, config.news_per_symbol
    )
    candidates = candidates.sort_values(
        ["combined_score", "return_1d", "return_5d"], ascending=False
    ).head(config.top_n)
    candidates = candidates.reset_index(drop=True)
    candidates.insert(0, "rank", np.arange(1, len(candidates) + 1))
    candidates["signal"] = candidates.apply(_signal_label, axis=1)
    candidates["research_status"] = "Idea candidate — deeper research required"

    coverage_ratio = len(histories) / len(universe)
    scan_status = (
        "COMPLETE"
        if coverage_ratio >= 0.80 and len(candidates) == config.top_n
        else "PARTIAL"
    )
    if scan_status == "PARTIAL":
        warnings.append(
            "Scan marked PARTIAL because usable price coverage was below 80% or fewer than the requested candidates passed all gates."
        )
    candidates["scan_status"] = scan_status
    candidates["history_coverage_ratio"] = coverage_ratio
    stats = {
        "universe_size": len(universe),
        "history_coverage": len(histories),
        "history_coverage_ratio": coverage_ratio,
        "price_filter_pass": len(metrics),
        "market_cap_lookups": cursor,
        "market_cap_pass": len(eligible),
        "final_candidates": len(candidates),
        "scan_status": scan_status,
    }
    return ScanResult(candidates, generated_at, config, stats, warnings)
