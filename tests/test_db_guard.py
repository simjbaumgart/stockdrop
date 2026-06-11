# tests/test_db_guard.py
"""The autouse guard in conftest.py must ensure no test ever sees the
production subscribers.db as its database — regardless of import-order
games other test modules play with app.database.DB_NAME."""

import os


def test_db_name_never_production():
    import app.database as db
    assert os.path.basename(str(db.DB_NAME)) != "subscribers.db", (
        "app.database.DB_NAME points at production inside a test"
    )


def test_db_path_env_never_production():
    # deep_research_service._apply_trading_level_overrides reads DB_PATH
    # from the environment at call time — it must be redirected too.
    assert os.getenv("DB_PATH", "subscribers.db") != "subscribers.db"
