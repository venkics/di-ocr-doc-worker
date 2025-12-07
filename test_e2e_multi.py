import os
import time
import uuid
import requests
import sys
from azure.storage.blob import BlobServiceClient

# Configuration
API_BASE = "https://ca-di-ocr-v2.purpleisland-df31fd5d.uksouth.azurecontainerapps.io"
STORAGE_CONN_STR = os.environ.get("STORAGE_ACCOUNT_KEY") # We'll fetch this from the environment or hardcode for the test if needed, but better to reuse existing env vars if possible. 
# Actually, the previous script used a hardcoded key or env var. Let's check test_e2e_containers.py to see how it did it.
# It used: STORAGE_KEY = "..." (hardcoded in the previous script I viewed, or maybe I should just use the one from deploy_containers.sh output)
# I will use the one from the previous successful runs.

STORAGE_CONNECTION_STRING = os.environ.get("STORAGE_CONNECTION_STRING")
INCOMING_CONTAINER = "incoming"

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_e2e_multi.py <file1> <file2> ...")
        sys.exit(1)

    file_paths = sys.argv[1:]
    
    # Validate files exist
    for fp in file_paths:
        if not os.path.exists(fp):
            print(f"Error: File not found: {fp}")
            sys.exit(1)

    print(f"--- Starting Multi-Doc E2E Test (CONTAINER APPS) ---")
    print(f"Target API: {API_BASE}")
    print(f"Files: {file_paths}")

    # 1. Create Batch
    print("1. Creating batch...")
    try:
        r = requests.post(f"{API_BASE}/v1/batches", json={"patient_id": "multi-doc-test"})
        r.raise_for_status()
        batch_data = r.json()
        batch_id = batch_data["batch_id"]
        prefix = batch_data["upload"]["required_path_prefix"]
        print(f"   Batch ID: {batch_id}")
        print(f"   Prefix: {prefix}")
    except Exception as e:
        print(f"   Failed to create batch: {e}")
        sys.exit(1)

    # 2. Upload Files
    print("2. Uploading files to Blob Storage...")
    try:
        blob_service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
            credential=STORAGE_KEY
        )
        container_client = blob_service.get_container_client(INCOMING_CONTAINER)

        for fp in file_paths:
            filename = os.path.basename(fp)
            blob_name = f"{prefix}{filename}"
            print(f"   Uploading {filename} -> {blob_name} ...")
            
            with open(fp, "rb") as data:
                container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        
        print("   All uploads successful.")
    except Exception as e:
        print(f"   Failed to upload files: {e}")
        sys.exit(1)

    # 3. Finalize Batch
    print("3. Finalizing batch...")
    try:
        r = requests.post(f"{API_BASE}/v1/batches/{batch_id}/finalize")
        r.raise_for_status()
        print("   Batch finalized successfully!")
    except Exception as e:
        print(f"   Failed to finalize batch: {e}")
        sys.exit(1)

    print(f"\nSUCCESS! Batch {batch_id} submitted.")
    print("4. Polling for completion...")

    start_time = time.time()
    while True:
        elapsed = int(time.time() - start_time)
        try:
            r = requests.get(f"{API_BASE}/v1/batches/{batch_id}")
            if r.status_code == 200:
                data = r.json()
                status = data.get("status")
                print(f"   [{elapsed}s] Status: {status}")
                
                if status == "succeeded":
                    print("\nBatch Processing Complete!")
                    print(f"Total Duration: {elapsed} seconds")
                    break
                elif status == "failed":
                    print("\nBatch Failed!")
                    break
            else:
                print(f"   [{elapsed}s] Status check failed: {r.status_code}")
        except Exception as e:
            print(f"   [{elapsed}s] Error checking status: {e}")

        time.sleep(5)

if __name__ == "__main__":
    main()
