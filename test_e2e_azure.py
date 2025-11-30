import requests
import sys
import os
import json

# Configuration - Azure Function URL
API_URL = "https://di-ocr-func-app-1764481450.azurewebsites.net"
FILE_PATH = "/Users/venkitachalamsubramanian/Desktop/Tech_Lotus/di-ocr-doc-worker/Test_docs/Roberts bloods.pdf"

def run_test():
    # 1. Validate File
    if not os.path.exists(FILE_PATH):
        print(f"Error: File not found at {FILE_PATH}")
        return

    filename = os.path.basename(FILE_PATH)
    print(f"--- Starting End-to-End Test for {filename} (AZURE) ---")
    print(f"Target API: {API_URL}")

    # 2. Create Batch
    print(f"1. Creating batch...")
    try:
        resp = requests.post(f"{API_URL}/v1/batches", json={"patient_id": "test-patient-e2e-azure"})
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
    print("   Batch finalized successfully!")
    print(f"\nSUCCESS! Batch {batch_id} has been submitted to Azure.")
    
    # 6. Poll for Completion
    print("4. Polling for completion...")
    import time
    
    max_retries = 30
    for i in range(max_retries):
        try:
            status_resp = requests.get(f"{API_URL}/v1/batches/{batch_id}")
            status_resp.raise_for_status()
            status_data = status_resp.json()
            
            batch_status = status_data.get("status")
            progress = status_data.get("progress", {})
            succeeded = progress.get("succeeded", 0)
            failed = progress.get("failed", 0)
            total = progress.get("total", 0)
            
            print(f"   [{i+1}/{max_retries}] Status: {batch_status} | Succeeded: {succeeded}/{total} | Failed: {failed}")
            
            if batch_status == "succeeded" or (total > 0 and succeeded == total):
                print("\n✅ WORKER SUCCESS! Document processed successfully.")
                print(f"   Output: {json.dumps(status_data, indent=2)}")
                return
            
            if failed > 0:
                print("\n❌ WORKER FAILED! One or more documents failed.")
                print(f"   Details: {json.dumps(status_data, indent=2)}")
                return
                
        except Exception as e:
            print(f"   Error polling status: {e}")
            
        time.sleep(5)
        
    print("\n⚠️ TIMEOUT: Worker did not finish in time.")

if __name__ == "__main__":
    run_test()
