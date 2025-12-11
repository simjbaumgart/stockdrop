import json
import os

def extract_env_vars():
    file_path = 'service_account.json'
    if not os.path.exists(file_path):
        print(f"File {file_path} not found.")
        return

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)

        print("\n# Google Cloud Credentials (Individual Variables)")
        print(f"GOOGLE_PROJECT_ID={data.get('project_id')}")
        print(f"GOOGLE_PRIVATE_KEY_ID={data.get('private_key_id')}")
        # Use repr to handle newlines safely, then strip quotes
        private_key = data.get('private_key', '').replace('\n', '\\n')
        print(f"GOOGLE_PRIVATE_KEY={private_key}")
        print(f"GOOGLE_CLIENT_EMAIL={data.get('client_email')}")
        print(f"GOOGLE_CLIENT_ID={data.get('client_id')}")
        print(f"GOOGLE_CLIENT_X509_CERT_URL={data.get('client_x509_cert_url')}")
        
        print("\n# Copy the above values to your Render Environment Variables or .env file.")

    except Exception as e:
        print(f"Error parsing JSON: {e}")

if __name__ == "__main__":
    extract_env_vars()
