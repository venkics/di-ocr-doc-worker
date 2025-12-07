
import requests
import sys
import os
import json

# Configuration
API_BASE = "https://di-ocr-prod-v2.azurewebsites.net"
PDF_PATH = "Test_docs/ref letter (2).pdf"

def run_test():
    # 1. Validate File

    filename = os.path.basename(PDF_PATH)
    print(f"--- Starting End-to-End Test for {filename} (AZURE) ---")
    print(f"Target API: {API_BASE}")

    # 2. Create Batch
    print(f"1. Creating batch...")
    try:
        resp = requests.post(f"{API_BASE}/v1/batches", json={"patient_id": "test-patient-e2e-azure"})
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
    if '?' in sas_url_full:
        base_url, sas_token = sas_url_full.split('?', 1)
    else:
        base_url = sas_url_full
        sas_token = ""
        
    blob_url = f"{base_url}/{prefix}{filename}?{sas_token}"
    
    # 4. Upload File
    print(f"2. Uploading file to Blob Storage...")
    upload_success = False
    for attempt in range(3):
        try:
            with open(PDF_PATH, 'rb') as f:
                headers = {
                    'x-ms-blob-type': 'BlockBlob',
                    'Content-Type': 'application/pdf'
                }
                upload_resp = requests.put(blob_url, data=f, headers=headers, timeout=60)
                upload_resp.raise_for_status()
                upload_success = True
                break
        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            print(f"   [Attempt {attempt+1}/3] Upload failed: {e}")
            time.sleep(2)
    
    if not upload_success:
        print("   Failed to upload file after 3 attempts.")
        return
    
    print("   Upload successful.")

    # 5. Finalize Batch
    print(f"3. Finalizing batch...")
    try:
        fin_resp = requests.post(f"{API_BASE}/v1/batches/{batch_id}/finalize")
        fin_resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to finalize batch: {e}")
        if fin_resp: print(fin_resp.text)
        return
    
    print("   Batch finalized successfully!")
    print("   Batch finalized successfully!")
    print(f"\nSUCCESS! Batch {batch_id} has been submitted to Azure.")
    
    # 4. Poll for completion
    print("4. Polling for completion...")
    import time
    status_url = f"{API_BASE}/v1/batches/{batch_id}"
    for _ in range(60): # Poll for 300 seconds (5 mins)
        try:
            resp = requests.get(status_url)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            print(f"   Status: {status}")
            
            if status == "succeeded":
                print("   Batch processing succeeded!")
                return batch_id
            elif status == "failed":
                print("   Batch processing failed!")
                return batch_id
        except Exception as e:
            print(f"   Error polling status: {e}")
        time.sleep(5)
        
    print("\n⚠️ TIMEOUT: Worker did not finish in time.")
    return batch_id

if __name__ == "__main__":
    run_test()
