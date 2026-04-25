"""DR verification claims must carry a URL. Missing URLs demote the claim to
UNVERIFIED so hallucinated disputes stop earning score adjustments."""

from app.services.deep_research_service import (
    normalize_verification_results,
    score_verification_penalty,
)


def test_entry_with_valid_url_is_kept_as_disputed():
    raw = [
        {
            "claim": "Revenue fell 12% QoQ",
            "verdict": "DISPUTED",
            "source_url": "https://www.sec.gov/some-filing.htm",
        }
    ]
    out = normalize_verification_results(raw)
    assert len(out) == 1
    assert out[0]["verdict"] == "DISPUTED"
    assert out[0]["source_url"].startswith("https://")


def test_entry_missing_url_is_downgraded_to_unverified():
    raw = [{"claim": "Some claim", "verdict": "VERIFIED", "source_url": ""}]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0].get("downgrade_reason") == "missing_source_url"


def test_legacy_string_entry_is_downgraded_to_unverified():
    """Old-format strings like 'Claim 1: [DISPUTED] — explanation' have no URL
    and must not earn a penalty."""
    raw = ["Claim 1: [DISPUTED] — explanation"]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0].get("downgrade_reason") == "legacy_string_format"


def test_invalid_url_scheme_is_downgraded():
    raw = [{"claim": "X", "verdict": "VERIFIED", "source_url": "not-a-url"}]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0]["downgrade_reason"] == "invalid_source_url"


def test_unknown_verdict_is_downgraded():
    raw = [{"claim": "X", "verdict": "MAYBE", "source_url": "https://x.com"}]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0]["downgrade_reason"].startswith("unknown_verdict")


def test_penalty_only_applies_to_grounded_disputes():
    normalized = [
        {"claim": "a", "verdict": "DISPUTED", "source_url": "https://x.com"},
        {"claim": "b", "verdict": "UNVERIFIED", "source_url": "",
         "downgrade_reason": "missing_source_url"},
        {"claim": "c", "verdict": "VERIFIED", "source_url": "https://y.com"},
    ]
    penalty = score_verification_penalty(normalized)
    assert penalty == -5  # one DISPUTED, others ignored


def test_none_or_empty_input_is_safe():
    assert normalize_verification_results(None) == []
    assert normalize_verification_results([]) == []
    assert score_verification_penalty([]) == 0
