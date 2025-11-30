# Azure Functions Migration & Usage Guide

This repository has been migrated from a container-based architecture to a serverless **Azure Functions** architecture. This shift improves scalability, reduces costs by running only on-demand, and simplifies deployment.

## 🚀 Architecture Overview

The system is split into two Azure Function Apps:

1.  **`di-ocr` (HTTP Trigger)**
    *   **Role**: The entry point / API Gateway.
    *   **Functionality**: Receives document processing requests, creates a batch, uploads files to Azure Blob Storage, and sends a message to the Service Bus queue.
    *   **Trigger**: HTTP `POST /api/process_batch` (or similar).

2.  **`doc-worker` (Service Bus Trigger)**
    *   **Role**: The background processor.
    *   **Functionality**: Listens to the `doc-ingest-af` queue. When a message arrives, it downloads the document, runs the OCR & LLM pipeline (`llm_doc_pipeline`), and saves the results back to Azure Blob Storage (`processed` container).
    *   **Trigger**: Service Bus Queue (`doc-ingest-af`).

### Data Flow
1.  **Client** sends a request to `di-ocr`.
2.  **`di-ocr`** uploads the file to Blob Storage (`incoming` container) and queues a message.
3.  **`doc-worker`** picks up the message, processes the file using Azure Document Intelligence and OpenAI.
4.  **`doc-worker`** writes the output (JSON, Excel) to Blob Storage (`processed` container).

---

## 🛠️ Prerequisites

*   **Azure CLI** (`az login`)
*   **Azure Functions Core Tools** (`func`)
*   **Python 3.11**
*   **Git**

---

## 📦 Deployment

We have automated the deployment process with shell scripts.

### 1. Initial Deployment
Run the deployment script to create all Azure resources (Resource Group, Storage Account, Service Bus, Function Apps) and deploy the code.

```bash
./deploy_to_azure.sh
```

### 2. Configuration Sync
After deployment, you must sync your local secrets (API keys, connection strings) to the Azure Function Apps.

1.  Update `sync_settings.sh` with your actual secrets (replace `<YOUR_...>` placeholders).
2.  Run the script:

```bash
./sync_settings.sh
```

### 3. Redeploying Worker Only
If you only made changes to the worker logic (e.g., `graph.py`), you can use the faster redeployment script:

```bash
./redeploy_worker.sh
```

---

## 🔌 Endpoints & Usage

### Base URL
You can find the Base URL in the output of `deploy_to_azure.sh` or in the Azure Portal.
Format: `https://<ocr-app-name>.azurewebsites.net`

### 1. Create Batch
**Endpoint**: `POST /v1/batches`
**Body**: `{"patient_id": "optional-id", "sas_ttl_minutes": 30}`
**Response**: Returns `batch_id` and a SAS URL for uploading files.

### 2. Upload Files
Use the `sas_url` from the previous step to upload your documents (PDFs) to Azure Blob Storage using a standard HTTP PUT request.

### 3. Finalize Batch (Trigger Processing)
**Endpoint**: `POST /v1/batches/{batch_id}/finalize`
**Response**: Confirms that documents have been queued for processing.

### 4. Get Status / Result
**Endpoint**: `GET /v1/batches/{batch_id}`
**Response**:
*   **202 Accepted**: Processing is still in progress. Returns status counts (queued, running, succeeded, failed).
*   **200 OK**: Processing complete. Returns the final consolidated JSON report.

---

## 🧪 Testing

We have an end-to-end test script that verifies the entire flow on Azure.

```bash
python test_e2e_azure.py
```

This script will:
1.  Create a batch via the `di-ocr` API.
2.  Upload a test PDF.
3.  Trigger the processing.
4.  Poll the status until completion.

---

## ⚠️ Key Migration Notes & Troubleshooting

### Read-Only File System
Azure Functions run in a read-only environment.
*   **Issue**: The worker tried to write output to `/home/site/wwwroot/output`.
*   **Fix**: We updated `graph.py` to write to `tempfile.gettempdir()` (usually `/tmp`) which is writable.

### Dependency Management
*   **Issue**: Subprocesses (like `subprocess.run`) do not automatically inherit the Azure Functions `PYTHONPATH`.
*   **Fix**: We patched `graph.py` to explicitly pass `sys.path` to the subprocess environment, ensuring it can find installed packages like `requests`.

### Service Bus Queue
*   The queue name was updated to `doc-ingest-af` to distinguish it from previous deployments.
