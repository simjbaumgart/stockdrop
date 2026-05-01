from pathlib import Path

from app.services.news_digest_parser import (
    parse_finimize_daily,
    parse_ft_daily,
    parse_wsj_daily,
)

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
    assert parse_wsj_daily(FIXTURES / "nope.md") == []


def test_parse_wsj_daily_extracts_articles():
    articles = parse_wsj_daily(FIXTURES / "wsj_2026-04-22.md")
    assert len(articles) >= 8
    titles = [a.title for a in articles]
    assert any("Activist Hedge Fund" in t for t in titles)
    activist = next(a for a in articles if "Activist Hedge Fund" in a.title)
    assert activist.section == "markets"
    assert activist.url.startswith("https://www.wsj.com/")


def test_parse_wsj_daily_assigns_sections():
    articles = parse_wsj_daily(FIXTURES / "wsj_2026-04-22.md")
    sections = {a.section for a in articles}
    # Fixture spans Markets / US Business / World
    assert "markets" in sections
    assert "us business" in sections
    assert "world" in sections


def test_parse_wsj_daily_splits_byline_from_published():
    """WSJ raw lines are 'Published: 2026-04-22 12:00 UTC · AnnaMaria Andriotis'.
    The byline (after ' · ') must land in Article.byline, not Article.published."""
    articles = parse_wsj_daily(FIXTURES / "wsj_2026-04-22.md")
    activist = next(a for a in articles if "Activist Hedge Fund" in a.title)
    assert activist.byline == "AnnaMaria Andriotis"
    assert "·" not in activist.published
    assert "AnnaMaria" not in activist.published
    assert activist.published.endswith("UTC")


def test_parse_wsj_daily_handles_missing_byline():
    """Some WSJ entries (live coverage pages) have no '· Author' suffix.
    Those should parse cleanly with empty byline."""
    articles = parse_wsj_daily(FIXTURES / "wsj_2026-04-22.md")
    live = next(a for a in articles if "Stock Market Today" in a.title)
    assert live.byline == ""
    assert live.published.endswith("UTC")


def test_parse_wsj_daily_uuid_falls_back_to_url_slug():
    """WSJ URLs don't carry an FT-style hex UUID. Parser should fall back to
    the slug from the URL path (mirroring Finimize behaviour)."""
    articles = parse_wsj_daily(FIXTURES / "wsj_2026-04-22.md")
    activist = next(a for a in articles if "Activist Hedge Fund" in a.title)
    # Slug is the last path component without query string
    assert activist.uuid.startswith("activist-hedge-fund-makes-nearly-3-billion-offer")
