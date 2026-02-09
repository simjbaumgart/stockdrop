import os
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

class GoogleDriveService:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
    SERVICE_ACCOUNT_FILE = 'service_account.json'
    SPREADSHEET_NAME = 'Stock Tracker Data'
    FOLDER_ID = '1tSvhvXdF_mCX1MbPEngDfByH6E2TdHFy'

    def __init__(self):
        self.creds = None
        self.sheets_service = None
        self.drive_service = None
        self._quota_exceeded = False
        self._authenticate()

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

    def _get_or_create_spreadsheet(self):
        if not self.drive_service:
            return None

        # Search for existing spreadsheet in the specific folder
        query = f"name = '{self.SPREADSHEET_NAME}' and '{self.FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
        results = self.drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])

        if files:
            print(f"Found existing spreadsheet: {files[0]['name']} ({files[0]['id']})")
            return files[0]['id']
        else:
            # Create new spreadsheet in the specific folder
            file_metadata = {
                'name': self.SPREADSHEET_NAME,
                'mimeType': 'application/vnd.google-apps.spreadsheet',
                'parents': [self.FOLDER_ID]
            }
            # We use drive_service to create the file with parent, then sheets service to format if needed
            # But sheets.create doesn't support parents directly easily without extra steps or using drive.create
            # Easier to use drive.files.create with mimeType spreadsheet
            
            try:
                file = self.drive_service.files().create(body=file_metadata, fields='id').execute()
                print(f"Created new spreadsheet: {self.SPREADSHEET_NAME} ({file.get('id')})")
                return file.get('id')
            except Exception as e:
                print(f"Error creating spreadsheet: {e}")
                return None

    def upload_data(self, data_dict):
        """
        Appends data to the Google Sheet.
        data_dict: { "Index Name": { "price": float, ... }, ... }
        """
        if self._quota_exceeded:
            return
        if not self.sheets_service:
            print("Sheets service not initialized. Skipping upload.")
            return

        spreadsheet_id = self._get_or_create_spreadsheet()
        if not spreadsheet_id:
            print("Could not get or create spreadsheet.")
            return

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare header and row
        # We want a consistent order. Let's sort by key.
        sorted_keys = sorted(data_dict.keys())
        
        # Check if header exists (simple check: if new file, add header)
        # For simplicity, we'll just append a row. If it's the first time, we might want headers.
        # Let's just append values. The user can add headers or we can try to read first.
        
        values = [timestamp]
        for key in sorted_keys:
            # Extract price from the dict
            item = data_dict.get(key, {})
            price = item.get('price', 0.0)
            values.append(price)

        # We also need to ensure we have headers if the sheet is empty
        # But reading first adds latency. Let's just append.
        # Actually, let's try to append a header row if it's a new file (created just now).
        # But _get_or_create_spreadsheet doesn't return if it was created.
        # Let's just assume the user can figure out the columns: Timestamp, Index1, Index2...
        
        body = {
            'values': [values]
        }
        
        try:
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body).execute()
            print(f"{result.get('updates').get('updatedCells')} cells appended.")
        except Exception as e:
            if "storageQuotaExceeded" in str(e):
                self._quota_exceeded = True
                print("[Google Drive] Storage quota exceeded. All Drive uploads disabled for this session.")
            else:
                print(f"Error appending data to sheet: {e}")

    def save_decision(self, decision_data: dict):
        """
        Appends a decision record to the Google Sheet.
        decision_data: {
            "symbol": str,
            "price": float,
            "change_percent": float,
            "recommendation": str,
            "reasoning": str,
            "pe_ratio": float,
            "market_cap": float,
            "sector": str,
            "region": str
        }
        """
        if self._quota_exceeded:
            return
        if not self.sheets_service:
            print("Sheets service not initialized. Skipping save_decision.")
            return

        spreadsheet_id = self._get_or_create_spreadsheet()
        if not spreadsheet_id:
            print("Could not get or create spreadsheet.")
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
            decision_data.get("region", "")
        ]
        
        body = {
            'values': [values]
        }
        
        try:
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body).execute()
            print(f"Saved decision for {decision_data.get('symbol')} to Drive.")
        except Exception as e:
            if "storageQuotaExceeded" in str(e):
                self._quota_exceeded = True
                print("[Google Drive] Storage quota exceeded. All Drive uploads disabled for this session.")
            else:
                print(f"Error saving decision to Drive: {e}")

drive_service = GoogleDriveService()
