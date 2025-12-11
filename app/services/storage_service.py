import os
import datetime
import csv
import io
import pathlib
from google.cloud import storage
from google.oauth2 import service_account

class GoogleStorageService:
    def __init__(self):
        self.service_account_file = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service_account.json')
        self.google_credentials_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
        self.bucket_name = os.getenv('GOOGLE_STORAGE_BUCKET')
        self.client = None
        self.bucket = None
        self._authenticate()

    def _authenticate(self):
        # Priority 1: GOOGLE_CREDENTIALS_JSON (Env Var String)
        if self.google_credentials_json:
            try:
                import json
                info = json.loads(self.google_credentials_json)
                self.creds = service_account.Credentials.from_service_account_info(info)
                self.client = storage.Client(credentials=self.creds, project=self.creds.project_id)
                print("Authenticated with Google Cloud Storage via GOOGLE_CREDENTIALS_JSON.")
                return
            except Exception as e:
                print(f"Error authenticating via GOOGLE_CREDENTIALS_JSON: {e}")

        # Priority 2: Individual Environment Variables
        project_id = os.getenv('GOOGLE_PROJECT_ID')
        private_key = os.getenv('GOOGLE_PRIVATE_KEY')
        client_email = os.getenv('GOOGLE_CLIENT_EMAIL')
        
        if project_id and private_key and client_email:
            try:
                # Handle potential escaped newlines in private key from env vars
                if "\\n" in private_key:
                    private_key = private_key.replace("\\n", "\n")
                
                info = {
                    "type": "service_account",
                    "project_id": project_id,
                    "private_key_id": os.getenv('GOOGLE_PRIVATE_KEY_ID'),
                    "private_key": private_key,
                    "client_email": client_email,
                    "client_id": os.getenv('GOOGLE_CLIENT_ID'),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": os.getenv('GOOGLE_CLIENT_X509_CERT_URL')
                }
                self.creds = service_account.Credentials.from_service_account_info(info)
                self.client = storage.Client(credentials=self.creds, project=self.creds.project_id)
                print("Authenticated with Google Cloud Storage via individual environment variables.")
                return
            except Exception as e:
                print(f"Error authenticating via individual environment variables: {e}")

        # Priority 3: File path
        if self.service_account_file and os.path.exists(self.service_account_file):
            try:
                self.creds = service_account.Credentials.from_service_account_file(
                    self.service_account_file)
                self.client = storage.Client(credentials=self.creds, project=self.creds.project_id)
                print(f"Authenticated with Google Cloud Storage via file {self.service_account_file}.")
            except Exception as e:
                print(f"Error authenticating with Google Cloud Storage file: {e}")
        else:
            print(f"Service account file {self.service_account_file} not found and GOOGLE_CREDENTIALS_JSON not set. Storage upload disabled.")

    def upload_data(self, data_dict):
        """
        Uploads data to a CSV file in Google Cloud Storage.
        data_dict: { "Index Name": { "price": float, ... }, ... }
        """
        if not self.client:
            print("Storage client not initialized. Skipping upload.")
            return

        try:
            self.bucket = self.client.bucket(self.bucket_name)
        except Exception as e:
            print(f"Error accessing bucket {self.bucket_name}: {e}")
            return

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        file_name = f"stock_data_{date_str}.csv"
        
        # Prepare CSV content
        sorted_keys = sorted(data_dict.keys())
        
        # Check if file exists to append or create new
        blob = self.bucket.blob(file_name)
        
        new_content = io.StringIO()
        writer = csv.writer(new_content)
        
        # If file doesn't exist, write headers
        if not blob.exists():
            headers = ["Timestamp"] + sorted_keys
            writer.writerow(headers)
        else:
            # If we want to append, we need to download, append, and re-upload
            # GCS objects are immutable, so "appending" means rewriting.
            # For simplicity and performance, maybe we just download the existing content?
            # Or better: just write a NEW file for every upload? No, that's too many files.
            # Let's download existing content.
            try:
                existing_content = blob.download_as_text()
                new_content.write(existing_content)
            except Exception as e:
                print(f"Error reading existing file: {e}")

        # Write new row
        row = [timestamp]
        for key in sorted_keys:
            item = data_dict.get(key, {})
            price = item.get('price', 0.0)
            row.append(price)
        
        writer.writerow(row)
        
        # Upload
        try:
            blob.upload_from_string(new_content.getvalue(), content_type='text/csv')
            print(f"Uploaded data to {self.bucket_name}/{file_name}")
        except Exception as e:
            print(f"Error uploading to GCS: {e}")

    def save_locally(self, data_dict):
        """
        Saves data to a local CSV file.
        data_dict: { "Index Name": { "price": float, ... }, ... }
        """
        try:
            # Create directory if it doesn't exist
            base_dir = pathlib.Path(os.getenv("DATA_DIR", "data"))
            backup_dir = base_dir / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            file_path = backup_dir / f"stock_data_{date_str}.csv"
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            sorted_keys = sorted(data_dict.keys())
            file_exists = file_path.exists()
            
            with open(file_path, mode='a', newline='') as f:
                writer = csv.writer(f)
                
                if not file_exists:
                    headers = ["Timestamp"] + sorted_keys
                    writer.writerow(headers)
                
                row = [timestamp]
                for key in sorted_keys:
                    item = data_dict.get(key, {})
                    price = item.get('price', 0.0)
                    row.append(price)
                
                writer.writerow(row)
                
            print(f"Saved local backup to {file_path}")
            
        except Exception as e:
            print(f"Error saving local backup: {e}")

    def save_decision_locally(self, decision_data: dict):
        """
        Saves decision data to a local CSV file.
        """
        try:
            # Create directory if it doesn't exist
            base_dir = pathlib.Path(os.getenv("DATA_DIR", "data"))
            backup_dir = base_dir / "decisions"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            file_path = backup_dir / f"decisions_{date_str}.csv"
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            file_exists = file_path.exists()
            
            with open(file_path, mode='a', newline='') as f:
                writer = csv.writer(f)
                
                if not file_exists:
                    headers = ["Timestamp", "Symbol", "Company Name", "Price", "Change Percent", "Recommendation", "Reasoning", "P/E Ratio", "Market Cap", "Sector", "Region", "Version"]
                    writer.writerow(headers)
                
                row = [
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
                    decision_data.get("git_version", "unknown")
                ]
                
                writer.writerow(row)
                
            print(f"Saved local decision record to {file_path}")
            
        except Exception as e:
            print(f"Error saving local decision record: {e}")

    def get_today_decisions(self) -> list:
        """
        Returns a list of symbols that have been processed today.
        """
        try:
            base_dir = pathlib.Path(os.getenv("DATA_DIR", "data"))
            backup_dir = base_dir / "decisions"
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            file_path = backup_dir / f"decisions_{date_str}.csv"
            
            if not file_path.exists():
                return []
                
            processed_symbols = []
            with open(file_path, mode='r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if "Symbol" in row:
                        processed_symbols.append(row["Symbol"])
            
            return processed_symbols
        except Exception as e:
            print(f"Error reading today's decisions: {e}")
            return []

storage_service = GoogleStorageService()
