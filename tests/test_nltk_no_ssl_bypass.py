"""Batch D: importing stock_service must NOT disable SSL certificate
verification process-wide, and NLTK punkt_tab must resolve from vendored
in-repo data without any network download.

Regression: stock_service used to set
    ssl._create_default_https_context = ssl._create_unverified_context
globally so DefeatBeta's import-time nltk.download could fetch punkt_tab.
That left every HTTPS call in the process unverified.
"""

import ssl


def test_stock_service_import_does_not_disable_ssl_verification():
    import app.services.stock_service  # noqa: F401

    # The process-wide default HTTPS context must still verify certificates.
    ctx = ssl._create_default_https_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "SSL cert verification disabled"
    assert ctx.check_hostname is True, "SSL hostname check disabled"


def test_punkt_tab_resolves_from_vendored_data():
    import app.services.stock_service  # noqa: F401
    import nltk

    # Must resolve from the in-repo .nltk_data dir — not a stale per-machine
    # copy in ~/nltk_data or /tmp. Raises LookupError if not on the path.
    pointer = nltk.data.find("tokenizers/punkt_tab/english")
    assert ".nltk_data" in str(pointer.path), (
        f"punkt_tab resolved from {pointer.path!r}, not the vendored repo data"
    )


def test_sentence_tokenizer_works_offline():
    import app.services.stock_service  # noqa: F401
    import nltk

    sentences = nltk.sent_tokenize("First sentence here. Second one follows.")
    assert sentences == ["First sentence here.", "Second one follows."]
