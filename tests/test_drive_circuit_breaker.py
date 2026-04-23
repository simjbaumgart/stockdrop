import datetime
from unittest.mock import MagicMock

import pytest

from app.services.drive_service import GoogleDriveService


@pytest.fixture
def svc(tmp_path):
    s = GoogleDriveService.__new__(GoogleDriveService)
    s.creds = None
    s.sheets_service = MagicMock()
    s.drive_service = MagicMock()
    s._breaker_state_path = str(tmp_path / "drive_breaker.json")
    s._consecutive_quota_failures = 0
    s._disabled_until = None
    return s


class TestCircuitBreaker:
    def test_single_quota_error_does_not_disable(self, svc):
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("storageQuotaExceeded: quota exceeded")
        )
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 1
        assert svc._disabled_until is None

    def test_three_quota_errors_trip_breaker(self, svc):
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("storageQuotaExceeded")
        )
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        for _ in range(3):
            svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 3
        assert svc._disabled_until is not None
        assert svc._disabled_until > datetime.datetime.utcnow()

    def test_successful_upload_resets_counter(self, svc):
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = [
            Exception("storageQuotaExceeded"),
            Exception("storageQuotaExceeded"),
            {"updates": {"updatedCells": 1}},
        ]
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        svc.upload_data({"AAPL": {"price": 1.0}})
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 2
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 0

    def test_disabled_window_short_circuits(self, svc):
        svc._disabled_until = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        svc._get_or_create_spreadsheet = MagicMock()
        svc.upload_data({"AAPL": {"price": 1.0}})
        svc._get_or_create_spreadsheet.assert_not_called()

    def test_expired_disabled_window_resets(self, svc):
        svc._disabled_until = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        svc._consecutive_quota_failures = 3
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.return_value = {
            "updates": {"updatedCells": 1}
        }
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 0
        assert svc._disabled_until is None


class TestPersistence:
    def test_state_persists_across_instances(self, svc):
        svc._consecutive_quota_failures = 3
        svc._disabled_until = datetime.datetime.utcnow() + datetime.timedelta(hours=12)
        svc._save_breaker_state()

        s2 = GoogleDriveService.__new__(GoogleDriveService)
        s2._breaker_state_path = svc._breaker_state_path
        s2._consecutive_quota_failures = 0
        s2._disabled_until = None
        s2._load_breaker_state()
        assert s2._consecutive_quota_failures == 3
        assert s2._disabled_until is not None


import os
from unittest.mock import patch


def test_drive_upload_enabled_false_disables_service():
    with patch.dict(os.environ, {"DRIVE_UPLOAD_ENABLED": "false"}):
        svc = GoogleDriveService()
        assert svc.sheets_service is None
        assert svc.drive_service is None
        assert getattr(svc, "_disabled_by_env", False) is True


def test_drive_upload_enabled_unset_preserves_default_path(tmp_path, monkeypatch):
    """When the flag is unset, behaviour is unchanged — service loads
    normally (though it may still be None if service_account.json is
    missing, via a different code path)."""
    monkeypatch.delenv("DRIVE_UPLOAD_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)  # no service_account.json here
    svc = GoogleDriveService()
    assert getattr(svc, "_disabled_by_env", False) is False


def test_get_or_create_spreadsheet_records_quota_failure_on_list_exception(tmp_path):
    svc = GoogleDriveService.__new__(GoogleDriveService)
    svc._consecutive_quota_failures = 0
    svc._disabled_until = None
    svc._breaker_state_path = str(tmp_path / "breaker.json")

    class BadDrive:
        def files(self):
            raise Exception("The user's Drive storage quota has been exceeded.")

    svc.drive_service = BadDrive()
    svc.FOLDER_ID = "x"
    svc.SPREADSHEET_NAME = "x"

    result = svc._get_or_create_spreadsheet()
    assert result is None
    assert svc._consecutive_quota_failures == 1
