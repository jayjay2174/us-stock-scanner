"""CSV and human-readable Markdown reports."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .core import ScanResult


@dataclass(frozen=True)
class ReportPaths:
    csv: Path
    markdown: Path
    archive_csv: Path
    archive_markdown: Path


def _percent(value: object) -> str:
    try:
        return f"{float(value):+.2%}"
    except (TypeError, ValueError):
        return "n/a"


def _market_cap(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if numeric >= 1_000_000_000:
        return f"${numeric / 1_000_000_000:.2f}B"
    return f"${numeric / 1_000_000:.0f}M"


def _escape_table(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_markdown(result: ScanResult) -> str:
    config = result.config
    generated = result.generated_at_utc.strftime("%Y-%m-%d %H:%M UTC")
    mode_note = "Synthetic offline demonstration" if config.data_mode == "demo" else "Live public data"
    lines = [
        "# NYSE/Nasdaq Strong-Stock Scan",
        "",
        f"Generated: **{generated}**  ",
        f"Mode: **{mode_note}**  ",
        f"Status: **{result.stats['scan_status']}**  ",
        (
            f"Hard filters: price **> ${config.min_price:,.2f}** and verified market cap "
            f"**> ${config.min_market_cap / 1_000_000:,.0f}m**"
        ),
        "",
        "> Research candidates only. This report is not investment advice and does not place trades.",
        "",
        "## Scan funnel",
        "",
        f"- Universe: {result.stats['universe_size']:,}",
        (
            f"- Usable price histories: {result.stats['history_coverage']:,} "
            f"({float(result.stats['history_coverage_ratio']):.1%} coverage)"
        ),
        f"- Passed price/history gate: {result.stats['price_filter_pass']:,}",
        f"- Market caps checked: {result.stats['market_cap_lookups']:,}",
        f"- Passed verified market-cap gate: {result.stats['market_cap_pass']:,}",
        f"- Final idea candidates: {result.stats['final_candidates']:,}",
        "",
    ]
    if result.warnings:
        lines.extend(["## Data warnings", ""])
        lines.extend(f"- {_escape_table(warning)}" for warning in result.warnings)
        lines.append("")

    lines.extend(
        [
            "## Top candidates",
            "",
            "| Rank | Symbol | Exchange | Price | Market cap | 1D | 5D | 20D | Vol/20D | RSI14 | Score | Signal |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for _, row in result.candidates.iterrows():
        lines.append(
            "| {rank} | {symbol} | {exchange} | ${price:.2f} | {market_cap} | {r1} | {r5} | "
            "{r20} | {volume:.2f}x | {rsi:.1f} | {score:.1f} | {signal} |".format(
                rank=int(row["rank"]),
                symbol=_escape_table(row["symbol"]),
                exchange=_escape_table(row["exchange"]),
                price=float(row["price"]),
                market_cap=_market_cap(row["market_cap"]),
                r1=_percent(row["return_1d"]),
                r5=_percent(row["return_5d"]),
                r20=_percent(row["return_20d"]),
                volume=float(row["volume_ratio_20d"]),
                rsi=float(row["rsi_14"]),
                score=float(row["combined_score"]),
                signal=_escape_table(row["signal"]),
            )
        )

    lines.extend(["", "## News context", ""])
    for _, row in result.candidates.iterrows():
        headline_text = str(row.get("headlines", "") or "").strip()
        if not headline_text:
            lines.append(f"- **{row['symbol']}** — No headline returned; news score is neutral.")
            continue
        headlines = [value.strip() for value in headline_text.split(" | ") if value.strip()]
        urls = [value.strip() for value in str(row.get("news_urls", "") or "").split(" | ")]
        rendered = []
        for index, headline in enumerate(headlines):
            url = urls[index] if index < len(urls) else ""
            rendered.append(f"[{headline}]({url})" if url else headline)
        lines.append(f"- **{row['symbol']}** — " + "; ".join(rendered))

    lines.extend(
        [
            "",
            "## Methodology and limitations",
            "",
            "The price/momentum score combines cross-sectional robust z-scores for 1-day, 5-day and "
            "20-day returns, relative volume, distance above the 20-day average and RSI. The final score "
            "weights price/momentum at 85% and a simple headline-keyword signal at 15%. Headline tone is "
            "a triage aid, not full sentiment analysis.",
            "",
            "The live universe comes from Nasdaq Trader symbol directories. Adjusted daily prices, market "
            "capitalization and headlines come from Yahoo Finance through yfinance. These free sources can "
            "be delayed, rate-limited, incomplete or subject to terms-of-use changes. Unknown market caps "
            "are excluded. Review primary filings and current market data before acting on any candidate.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(result: ScanResult, output_dir: Path) -> ReportPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = output_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    date_stamp = result.generated_at_utc.strftime("%Y-%m-%d")
    suffix = "-demo" if result.config.data_mode == "demo" else ""

    latest_csv = output_dir / f"latest{suffix}.csv"
    latest_markdown = output_dir / f"latest{suffix}.md"
    archive_csv = archive_dir / f"{date_stamp}{suffix}.csv"
    archive_markdown = archive_dir / f"{date_stamp}{suffix}.md"

    csv_frame = result.candidates.copy()
    for column in (
        "return_1d",
        "return_5d",
        "return_20d",
        "volume_ratio_20d",
        "price_vs_sma20",
        "sma20_vs_sma50",
        "distance_from_20d_high",
        "rsi_14",
        "price_momentum_score",
        "news_signal",
        "news_score",
        "combined_score",
    ):
        if column in csv_frame:
            csv_frame[column] = pd.to_numeric(csv_frame[column], errors="coerce").round(6)
    csv_frame.to_csv(latest_csv, index=False, encoding="utf-8")
    latest_markdown.write_text(render_markdown(result), encoding="utf-8")
    shutil.copy2(latest_csv, archive_csv)
    shutil.copy2(latest_markdown, archive_markdown)
    return ReportPaths(latest_csv, latest_markdown, archive_csv, archive_markdown)
