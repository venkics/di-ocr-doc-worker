import requests
import time
import sys
import os
import json
from azure.storage.blob import BlobServiceClient

# Configuration
API_BASE = "https://ca-di-ocr-v2.purpleisland-df31fd5d.uksouth.azurecontainerapps.io"
SERVICEBUS_CONNECTION_STRING = os.environ.get("SERVICEBUS_CONNECTION")
if not SERVICEBUS_CONNECTION_STRING:
    raise ValueError("Please set SERVICEBUS_CONNECTION env var") 
# Note: The above is SB conn string, but we need Storage Account Key/Name or SAS.
# The previous test script used env vars or hardcoded keys.
# Let's check test_e2e_multi.py to see how it uploads.

# Re-reading test_e2e_multi.py to copy the upload logic exactly.
# It seems I need the storage account key.
# From previous context:
STORAGE_ACCOUNT_NAME = "stdocpipelinev2uk"
STORAGE_ACCOUNT_KEY = os.environ.get("STORAGE_ACCOUNT_KEY")
if not STORAGE_ACCOUNT_KEY:
    # Fallback or raise
    pass

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_e2e_image.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"File not found: {image_path}")
        sys.exit(1)

    print("--- Starting Image E2E Test (CONTAINER APPS) ---")
    print(f"Target API: {API_BASE}")
    print(f"File: {image_path}")

    # 1. Create Batch
    print("1. Creating batch...")
    try:
        r = requests.post(f"{API_BASE}/v1/batches", json={"patient_id": "image-test"})
        r.raise_for_status()
        batch_data = r.json()
        batch_id = batch_data["batch_id"]
        blob_prefix = batch_data["upload"]["required_path_prefix"]
        print(f"   Batch ID: {batch_id}")
        print(f"   Prefix: {blob_prefix}")
    except Exception as e:
        print(f"FAILED to create batch: {e}")
        if 'r' in locals(): print(r.text)
        sys.exit(1)

    # 2. Upload File
    print("2. Uploading file to Blob Storage...")
    try:
        blob_service_client = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
            credential=STORAGE_ACCOUNT_KEY
        )
        container_client = blob_service_client.get_container_client("incoming")
        
        filename = os.path.basename(image_path)
        blob_name = f"{blob_prefix}{filename}"
        
        print(f"   Uploading {filename} -> {blob_name} ...")
        with open(image_path, "rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        print("   Upload successful.")
    except Exception as e:
        print(f"FAILED to upload file: {e}")
        sys.exit(1)

    # 3. Finalize Batch
    print("3. Finalizing batch...")
    try:
        r = requests.post(f"{API_BASE}/v1/batches/{batch_id}/finalize")
        r.raise_for_status()
        print("   Batch finalized successfully!")
    except Exception as e:
        print(f"FAILED to finalize batch: {e}")
        if 'r' in locals(): print(r.text)
        sys.exit(1)

    print(f"\nSUCCESS! Batch {batch_id} submitted.")
    print("4. Polling for completion...")

    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed > 300: # 5 min timeout
            print("Timeout waiting for batch completion.")
            break
            
        try:
            r = requests.get(f"{API_BASE}/v1/batches/{batch_id}")
            if r.status_code == 200:
                data = r.json()
                status = data.get("status")
                if status == "succeeded":
                    print(f"   Batch SUCCEEDED in {elapsed:.1f}s!")
                    print(json.dumps(data, indent=2))
                    break
                elif status == "failed":
                    print(f"   Batch FAILED!")
                    print(json.dumps(data, indent=2))
                    break
                else:
                    print(f"   [{elapsed:.0f}s] Status: {status}")
            else:
                print(f"   [{elapsed:.0f}s] Status check failed: {r.status_code}")
        except Exception as e:
            print(f"   Error polling: {e}")
        
        time.sleep(5)

if __name__ == "__main__":
    main()
