"""Shared company-name <-> text sanity matcher.

Defends against ticker collisions in two places: DefeatBeta transcripts for
the wrong company ('L' -> Loblaw instead of Loews) and search-grounded
council agents analyzing the wrong entity (IAG -> International Consolidated
Airlines instead of IAMGOLD). One matcher, one set of normalization rules.
"""
import re


def text_matches_company(text: str, expected_company: str) -> bool:
    """
    Defensive match: does the text reference the expected company?

    DefeatBeta's HuggingFace dataset has known ticker-collision bugs (e.g.
    'L' returns Loblaw instead of Loews). We verify the first 1500 chars
    of the transcript mention either the full expected name or its first
    significant token (modulo case, common corporate suffixes, and
    trailing punctuation/parentheticals).

    Real-world failures this normalization addresses:
    - 'MP Materials Corp.'  (trailing dot defeated endswith ' corp')
    - 'Gap, Inc. (The)'     (trailing parenthetical)
    - 'Vodafone Group Plc'  (case-only ' plc' suffix)

    Returns True if no expected_company was provided (backward compat).
    """
    if not expected_company:
        return True
    if not text:
        return False

    head = text[:1500].lower()
    expected_lower = expected_company.lower().strip()

    # Strip a trailing parenthetical like "(the)" or "(holdings)" first.
    expected_lower = re.sub(r"\s*\([^)]*\)\s*$", "", expected_lower).strip()

    # Strip trailing punctuation so " corp." matches the " corp" suffix.
    expected_lower = expected_lower.rstrip(" ,.")

    # Keep the pre-suffix-strip name as a fallback: short names like
    # "XP Inc." reduce to "XP" (2 chars) once " inc" is stripped, which
    # is below the disambiguation floor — but "xp inc" still matches.
    pre_strip_name = expected_lower

    # Suffix list (no trailing dots — we already stripped them above).
    suffixes = (
        " corporation", " corp", " incorporated", " inc",
        " plc", " ltd", " limited", " companies", " company", " co",
        " holdings", " group", " ag", " sa", " nv", " se",
    )
    # Strip repeatedly so "Holdings Corp" -> "Holdings" -> "" cleanly.
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if expected_lower.endswith(suffix):
                expected_lower = expected_lower[: -len(suffix)].rstrip(" ,.")
                changed = True
                break

    if len(expected_lower) < 3:
        # Suffix stripping shrank the name below the disambiguation
        # floor. If the pre-strip name (suffix included) is long enough,
        # match on that instead — handles short names like "XP Inc.".
        # Otherwise the input is degenerate (e.g. "(The)") — reject and
        # fall through to AV rather than accept any transcript.
        if len(pre_strip_name) >= 3:
            expected_lower = pre_strip_name
        else:
            return False

    # Match the full stripped name or its first significant token, but
    # only on a word boundary — naive `in` containment caused false
    # positives ("arco" inside "marco", "mp" inside "company").
    def _has_word(term: str) -> bool:
        return re.search(r"\b" + re.escape(term) + r"\b", head) is not None

    first_token = expected_lower.split()[0]
    if _has_word(expected_lower):
        return True
    return len(first_token) >= 3 and _has_word(first_token)
