import requests
import sys
import os
import json

# Configuration
API_URL = "http://localhost:7071"
FILE_PATH = "/Users/venkitachalamsubramanian/Desktop/Tech_Lotus/di-ocr-doc-worker/Test_docs/Roberts bloods.pdf"

def run_test():
    # 1. Validate File
    if not os.path.exists(FILE_PATH):
        print(f"Error: File not found at {FILE_PATH}")
        return

    filename = os.path.basename(FILE_PATH)
    print(f"--- Starting End-to-End Test for {filename} ---")

    # 2. Create Batch
    print(f"1. Creating batch...")
    try:
        resp = requests.post(f"{API_URL}/v1/batches", json={"patient_id": "test-patient-e2e"})
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to create batch: {e}")
        if resp: print(resp.text)
        return
    
    data = resp.json()
    batch_id = data['batch_id']
    upload_info = data['upload']
    sas_url_full = upload_info['sas_url']
    prefix = upload_info['required_path_prefix']
    
    print(f"   Batch ID: {batch_id}")
    print(f"   Prefix: {prefix}")

    # 3. Construct Blob URL
    # sas_url_full is like https://account.blob.core.windows.net/container?sas_token
    # We need https://account.blob.core.windows.net/container/{prefix}{filename}?sas_token
    
    if '?' in sas_url_full:
        base_url, sas_token = sas_url_full.split('?', 1)
    else:
        base_url = sas_url_full
        sas_token = ""
        
    blob_url = f"{base_url}/{prefix}{filename}?{sas_token}"
    
    # 4. Upload File
    print(f"2. Uploading file to Blob Storage...")
    try:
        with open(FILE_PATH, 'rb') as f:
            headers = {
                'x-ms-blob-type': 'BlockBlob',
                'Content-Type': 'application/pdf'
            }
            upload_resp = requests.put(blob_url, data=f, headers=headers)
            upload_resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to upload file: {e}")
        return
    
    print("   Upload successful.")

    # 5. Finalize Batch
    print(f"3. Finalizing batch...")
    try:
        fin_resp = requests.post(f"{API_URL}/v1/batches/{batch_id}/finalize")
        fin_resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to finalize batch: {e}")
        if fin_resp: print(fin_resp.text)
        return
    
    print("   Batch finalized successfully!")
    print(f"\nSUCCESS! Batch {batch_id} has been submitted.")
    print("Check your 'doc-worker' terminal to see the processing logs.")

if __name__ == "__main__":
    run_test()
