from app.utils.company_match import text_matches_company


def test_full_name_match():
    assert text_matches_company("IAMGOLD Corporation reported...", "IAMGOLD Corporation")


def test_first_token_match():
    assert text_matches_company("Welcome to the IAMGOLD earnings call", "IAMGOLD Corporation")


def test_wrong_company_rejected():
    airline = (
        "International Consolidated Airlines Group operates British Airways "
        "and Iberia across the North Atlantic corridor, competing with "
        "Lufthansa and Air France-KLM on fuel-hedged routes."
    )
    assert not text_matches_company(airline, "IAMGOLD Corporation")


def test_empty_expected_is_permissive():
    assert text_matches_company("anything", "")


def test_empty_text_rejected():
    assert not text_matches_company("", "IAMGOLD Corporation")


def test_head_window_vs_full_text():
    # Default transcript window is 1500 chars; head_chars=None searches all.
    text = ("x" * 1600) + " IAMGOLD Corporation"
    assert not text_matches_company(text, "IAMGOLD Corporation")
    assert text_matches_company(text, "IAMGOLD Corporation", head_chars=None)
