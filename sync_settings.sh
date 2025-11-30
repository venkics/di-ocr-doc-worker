#!/bin/bash

# App Names (from your deployment logs)
OCR_APP_NAME="di-ocr-func-app-1764481450"
WORKER_APP_NAME="doc-worker-func-app-1764481450"
RESOURCE_GROUP="rg-docpipeline-dev"

# Secrets / Configs (from your local.settings.json)
POSTGRES_URL="<YOUR_POSTGRES_URL>"
STORAGE_ACCOUNT_NAME="<YOUR_STORAGE_ACCOUNT_NAME>"
STORAGE_ACCOUNT_KEY="<YOUR_STORAGE_ACCOUNT_KEY>"
DI_ENDPOINT="<YOUR_DI_ENDPOINT>"
DI_KEY="<YOUR_DI_KEY>"
AOAI_ENDPOINT="<YOUR_AOAI_ENDPOINT>"
AOAI_API_KEY="<YOUR_AOAI_API_KEY>"
AOAI_API_VERSION="2024-06-01"
AOAI_CHAT_DEPLOYMENT="gpt-4.1"

# --- 1. DI-OCR App Settings ---
echo "Configuring DI-OCR App..."
func azure functionapp publish $OCR_APP_NAME --publish-settings-only
az functionapp config appsettings set --name $OCR_APP_NAME --resource-group $RESOURCE_GROUP --settings \
    POSTGRES_URL="<YOUR_POSTGRES_URL>" \
    AzureWebJobsStorage="<YOUR_STORAGE_CONNECTION_STRING>" \
    SERVICEBUS_CONNECTION="<YOUR_SERVICEBUS_CONNECTION_STRING>" \
    DOC_INGEST_QUEUE="doc-ingest-af"

# --- 2. Doc-Worker App Settings ---
echo "Configuring Doc-Worker App..."
func azure functionapp publish $WORKER_APP_NAME --publish-settings-only
az functionapp config appsettings set --name $WORKER_APP_NAME --resource-group $RESOURCE_GROUP --settings \
    AzureWebJobsStorage="<YOUR_STORAGE_CONNECTION_STRING>" \
    SERVICEBUS_CONNECTION="<YOUR_SERVICEBUS_CONNECTION_STRING>" \
    DOC_INGEST_QUEUE="doc-ingest-af" \
    INCOMING_CONTAINER="incoming" \
    PROCESSED_CONTAINER="processed" \
    SCOPE_XLSX_PATH="Scope_Workbook.xlsx" \
    DI_SCRIPT_PATH="llm_doc_pipeline/orchestrator/ocr/di_pages_merge.py" \
    DI_ENDPOINT="<YOUR_DI_ENDPOINT>" \
    DI_KEY="<YOUR_DI_KEY>" \
    AOAI_ENDPOINT="<YOUR_AOAI_ENDPOINT>" \
    AOAI_API_KEY="<YOUR_AOAI_API_KEY>" \
    AOAI_API_VERSION="2024-06-01" \
    AOAI_CHAT_DEPLOYMENT="gpt-4.1"

echo "Settings synced!"
