# API Consumption Guide (UI Integration)

This guide details how to integrate the Document Processing API into a frontend application.

## Base URL
The API is hosted at:
`https://ca-di-ocr-v2.purpleisland-df31fd5d.uksouth.azurecontainerapps.io`

---

## Workflow Overview
1.  **Create a Batch**: Initialize a session and get a `batch_id` and SAS URL for uploading files.
2.  **Upload Files**: Upload documents directly to Azure Blob Storage using the provided SAS URL.
3.  **Finalize Batch**: Notify the API that uploads are complete to trigger processing.
4.  **Poll Status**: Periodically check the status of the batch until completion.

---

## 1. Create a Batch
**Endpoint**: `POST /v1/batches`

**Request**:
```json
{
  "user_id": "user_123",
  "project_id": "proj_001"
}
```

**Response**:
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "container_url": "https://<storage>.blob.core.windows.net/incoming?sv=...",
  "blob_prefix": "550e8400-e29b-41d4-a716-446655440000/"
}
```
*   `batch_id`: Unique identifier for this processing job.
*   `container_url`: The SAS URL for the Azure Blob Container.
*   `blob_prefix`: The folder path where you **MUST** upload files for this batch.

---

## 2. Upload Files (Frontend Side)
Use the `container_url` and `blob_prefix` from Step 1 to upload files directly to Azure Blob Storage. You do **not** upload files to the API server.

**JavaScript Example (using `fetch` or Azure SDK)**:
To upload a file named `document.pdf`:

1.  Construct the full URL:
    `PUT {container_url_base}/{blob_prefix}document.pdf{sas_token_query_params}`
    *(Note: You need to parse the `container_url` to insert the blob path correctly before the query string)*

2.  **Headers**:
    *   `x-ms-blob-type`: `BlockBlob`
    *   `Content-Type`: `application/pdf` (or `image/jpeg`)

---

## 3. Finalize Batch
Once all files are uploaded successfully, call this endpoint to start processing.

**Endpoint**: `POST /v1/batches/{batch_id}/finalize`

**Request**:
```json
{}
```
*(Empty JSON body)*

**Response**:
```json
{
  "status": "queued",
  "message": "Batch finalized and processing started."
}
```

---

## 4. Poll for Status
Check the progress of the batch.

**Endpoint**: `GET /v1/batches/{batch_id}`

**Response (In Progress)**:
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "documents": [
    { "filename": "document.pdf", "status": "processing" }
  ]
}
```

**Response (Completed)**:
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "succeeded",
  "documents": [
    {
      "filename": "document.pdf",
      "status": "succeeded",
      "result": {
        "classification": { ... },
        "extraction": { ... }
      }
    }
  ]
}
```

### Status Values
*   `created`: Batch created, waiting for finalize.
*   `running`: Processing in progress.
*   `succeeded`: All documents processed successfully.
*   `failed`: One or more documents failed (check `error` field in document object).
*   `partial_success`: Some documents succeeded, some failed.
