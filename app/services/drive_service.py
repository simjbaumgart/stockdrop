import os
import json
import datetime
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build


class GoogleDriveService:
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.file',
    ]
    SERVICE_ACCOUNT_FILE = 'service_account.json'
    SPREADSHEET_NAME = 'Stock Tracker Data'
    FOLDER_ID = '1tSvhvXdF_mCX1MbPEngDfByH6E2TdHFy'

    QUOTA_FAILURES_TO_TRIP = 3
    DISABLED_DURATION = datetime.timedelta(hours=24)

    BREAKER_STATE_FILE = '.drive_breaker_state.json'

    def __init__(self):
        self.creds = None
        self.sheets_service = None
        self.drive_service = None
        self._breaker_state_path = self.BREAKER_STATE_FILE
        self._consecutive_quota_failures = 0
        self._disabled_until: Optional[datetime.datetime] = None
        self._disabled_by_env = os.getenv("DRIVE_UPLOAD_ENABLED", "true").lower() == "false"
        if self._disabled_by_env:
            print("[Google Drive] Upload disabled via DRIVE_UPLOAD_ENABLED=false.")
            return
        self._load_breaker_state()
        self._authenticate()

    def _load_breaker_state(self):
        try:
            if os.path.exists(self._breaker_state_path):
                with open(self._breaker_state_path) as f:
                    state = json.load(f)
                self._consecutive_quota_failures = int(state.get("consecutive_quota_failures", 0))
                dis = state.get("disabled_until")
                if dis:
                    parsed = datetime.datetime.fromisoformat(dis)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
                    self._disabled_until = parsed
                else:
                    self._disabled_until = None
        except Exception as e:
            print(f"[Google Drive] Could not load breaker state: {e}")

    def _save_breaker_state(self):
        try:
            state = {
                "consecutive_quota_failures": self._consecutive_quota_failures,
                "disabled_until": self._disabled_until.isoformat() if self._disabled_until else None,
            }
            with open(self._breaker_state_path, "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"[Google Drive] Could not save breaker state: {e}")

    def _authenticate(self):
        if os.path.exists(self.SERVICE_ACCOUNT_FILE):
            try:
                self.creds = service_account.Credentials.from_service_account_file(
                    self.SERVICE_ACCOUNT_FILE, scopes=self.SCOPES)
                self.sheets_service = build('sheets', 'v4', credentials=self.creds)
                self.drive_service = build('drive', 'v3', credentials=self.creds)
                print("Authenticated with Google Drive/Sheets.")
            except Exception as e:
                print(f"Error authenticating with Google Drive: {e}")
        else:
            print(f"Service account file {self.SERVICE_ACCOUNT_FILE} not found. Drive upload disabled.")

    def _breaker_tripped(self) -> bool:
        if self._disabled_until is None:
            return False
        if datetime.datetime.now(datetime.timezone.utc) >= self._disabled_until:
            self._consecutive_quota_failures = 0
            self._disabled_until = None
            self._save_breaker_state()
            return False
        return True

    def _record_quota_failure(self):
        self._consecutive_quota_failures += 1
        if self._consecutive_quota_failures >= self.QUOTA_FAILURES_TO_TRIP:
            self._disabled_until = datetime.datetime.now(datetime.timezone.utc) + self.DISABLED_DURATION
            print(
                f"[Google Drive] Circuit breaker tripped after "
                f"{self._consecutive_quota_failures} consecutive quota errors. "
                f"Disabled until {self._disabled_until.isoformat()}."
            )
        self._save_breaker_state()

    def _record_success(self):
        if self._consecutive_quota_failures:
            self._consecutive_quota_failures = 0
            self._save_breaker_state()

    def _get_or_create_spreadsheet(self):
        if not self.drive_service:
            return None
        if self._breaker_tripped():
            return None
        query = (
            f"name = '{self.SPREADSHEET_NAME}' and '{self.FOLDER_ID}' in parents "
            f"and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
        )
        try:
            results = self.drive_service.files().list(
                q=query, spaces='drive', fields='files(id, name)'
            ).execute()
        except Exception as e:
            self._record_quota_failure()
            # Demote to debug once breaker is tripped — expected noise during
            # a quota outage; don't log every subsequent call.
            if not self._breaker_tripped():
                print(f"Error listing spreadsheet: {e}")
            return None

        files = results.get('files', [])
        if files:
            return files[0]['id']
        file_metadata = {
            'name': self.SPREADSHEET_NAME,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [self.FOLDER_ID],
        }
        try:
            file = self.drive_service.files().create(body=file_metadata, fields='id').execute()
            print(f"Created new spreadsheet: {self.SPREADSHEET_NAME} ({file.get('id')})")
            self._record_success()
            return file.get('id')
        except Exception as e:
            self._record_quota_failure()
            if not self._breaker_tripped():
                print(f"Error creating spreadsheet: {e}")
            return None

    def upload_data(self, data_dict):
        if self._breaker_tripped():
            return
        if not self.sheets_service:
            return
        spreadsheet_id = self._get_or_create_spreadsheet()
        if not spreadsheet_id:
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sorted_keys = sorted(data_dict.keys())
        values = [timestamp] + [data_dict.get(k, {}).get('price', 0.0) for k in sorted_keys]
        body = {'values': [values]}
        try:
            self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body).execute()
            self._record_success()
        except Exception as e:
            if "storageQuotaExceeded" in str(e):
                self._record_quota_failure()
            else:
                print(f"Error appending data to sheet: {e}")

    def save_decision(self, decision_data: dict):
        if self._breaker_tripped():
            return
        if not self.sheets_service:
            return
        spreadsheet_id = self._get_or_create_spreadsheet()
        if not spreadsheet_id:
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = [
            timestamp,
            decision_data.get("symbol", ""),
            decision_data.get("company_name", ""),
            decision_data.get("price", 0.0),
            decision_data.get("change_percent", 0.0),
            decision_data.get("recommendation", ""),
            decision_data.get("reasoning", ""),
            decision_data.get("pe_ratio", 0.0),
            decision_data.get("market_cap", 0.0),
            decision_data.get("sector", ""),
            decision_data.get("region", ""),
        ]
        body = {'values': [values]}
        try:
            self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body).execute()
            self._record_success()
        except Exception as e:
            if "storageQuotaExceeded" in str(e):
                self._record_quota_failure()
            else:
                print(f"Error saving decision to Drive: {e}")


drive_service = GoogleDriveService()
