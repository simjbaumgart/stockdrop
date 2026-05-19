from app.services.stock_service import StockService

m = StockService._transcript_matches_company


def test_substring_false_positive_rejected():
    # "Arco" must NOT match because "marco" contains "arco"
    assert m("Welcome, this is the Marco Polo Group earnings call.", "Arco Platform Ltd") is False


def test_legit_full_name_matches():
    assert m("MP Materials Corp. fourth quarter earnings call", "MP Materials Corp.") is True


def test_legit_first_token_matches_on_boundary():
    assert m("Operator: Welcome to the Vodafone results presentation", "Vodafone Group Plc") is True


def test_short_normalized_name_rejected():
    # "(The)" normalizes to empty -> reject
    assert m("Some unrelated transcript text here", "(The)") is False


def test_empty_company_is_backward_compatible():
    assert m("anything", "") is True
