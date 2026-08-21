from pathlib import Path

from stock_scanner.core import ScanConfig, run_scan
from stock_scanner.providers import DemoMarketDataProvider, DemoNewsProvider, DemoUniverseProvider
from stock_scanner.reporting import write_reports


def test_demo_scan_enforces_filters_and_ranking(tmp_path: Path) -> None:
    config = ScanConfig(top_n=20, data_mode="demo", news_candidates=25)
    result = run_scan(
        config,
        DemoUniverseProvider(),
        DemoMarketDataProvider(),
        DemoNewsProvider(),
    )

    assert len(result.candidates) == 20
    assert result.candidates["price"].gt(config.min_price).all()
    assert result.candidates["market_cap"].gt(config.min_market_cap).all()
    assert result.candidates["combined_score"].is_monotonic_decreasing
    assert result.candidates["rank"].tolist() == list(range(1, 21))
    assert result.candidates["research_status"].str.contains("deeper research").all()
    assert result.stats["scan_status"] == "COMPLETE"

    paths = write_reports(result, tmp_path)
    assert paths.csv.exists()
    assert paths.markdown.exists()
    markdown = paths.markdown.read_text(encoding="utf-8")
    assert "Synthetic offline demonstration" in markdown
    assert "not investment advice" in markdown


def test_no_news_uses_neutral_news_score() -> None:
    config = ScanConfig(top_n=5, data_mode="demo", include_news=False)
    result = run_scan(
        config,
        DemoUniverseProvider(),
        DemoMarketDataProvider(),
        None,
    )
    assert result.candidates["news_score"].eq(50).all()
