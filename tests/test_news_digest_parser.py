from pathlib import Path

from app.services.news_digest_parser import parse_ft_daily, parse_finimize_daily

FIXTURES = Path(__file__).parent / "fixtures" / "news"


def test_parse_ft_daily_extracts_articles():
    articles = parse_ft_daily(FIXTURES / "ft_2026-04-22.md")
    assert len(articles) >= 15
    titles = [a.title for a in articles]
    assert "MSCI boots Indonesian tycoon-owned stocks from indices" in titles
    msci = next(a for a in articles if a.title.startswith("MSCI"))
    assert msci.uuid == "d37d17d2-e0ec-426d-bde7-bf18990d7a7c"
    assert msci.section == "markets"
    assert msci.url.startswith("https://www.ft.com/content/")
    assert "MSCI removed Barito" in msci.summary


def test_parse_ft_daily_assigns_sections():
    articles = parse_ft_daily(FIXTURES / "ft_2026-04-22.md")
    sections = {a.section for a in articles}
    assert {"markets", "companies", "opinion"} <= sections
    assert any(s.startswith("editorial") for s in sections)


def test_parse_finimize_daily_extracts_slug():
    articles = parse_finimize_daily(FIXTURES / "finimize_2026-04-22.md")
    assert len(articles) >= 5
    first = articles[0]
    assert first.uuid == "us-state-jobless-rates-barely-budged-in-february"
    assert "us" in first.tags


def test_parse_finimize_extracts_tickers():
    articles = parse_finimize_daily(FIXTURES / "finimize_2026-04-22.md")
    ksl = next((a for a in articles if "KSL" in a.title), None)
    assert ksl is not None
    assert "APO" in ksl.tickers


def test_parse_missing_file_returns_empty():
    assert parse_ft_daily(FIXTURES / "nope.md") == []
    assert parse_finimize_daily(FIXTURES / "nope.md") == []
