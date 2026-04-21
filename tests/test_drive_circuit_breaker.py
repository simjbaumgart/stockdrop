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
