#!/bin/bash

# Configuration - PLEASE UPDATE THESE BEFORE RUNNING
RESOURCE_GROUP="rg-docpipeline-dev"
LOCATION="eastus" # e.g., eastus, westeurope
STORAGE_ACCOUNT="stdocpipeline01" # Must be globally unique
SB_NAMESPACE="sb-docpipeline-dev" # Just the name, not the full URL

# Function App Names - Must be globally unique
OCR_APP_NAME="di-ocr-func-app-$(date +%s)"
WORKER_APP_NAME="doc-worker-func-app-$(date +%s)"

# Queue Name
QUEUE_NAME="doc-ingest-af"

echo "--- Starting Deployment Setup ---"
echo "Resource Group: $RESOURCE_GROUP"
echo "Storage Account: $STORAGE_ACCOUNT"
echo "OCR App: $OCR_APP_NAME"
echo "Worker App: $WORKER_APP_NAME"
echo "Queue: $QUEUE_NAME"

# 1. Create Service Bus Queue (if not exists)
# We create this specific queue because we just renamed it to 'doc-ingest-af'
echo "Creating/Checking Service Bus Queue '$QUEUE_NAME'..."
az servicebus queue create --resource-group $RESOURCE_GROUP --namespace-name $SB_NAMESPACE --name $QUEUE_NAME || echo "Queue might already exist or creation failed. Continuing..."

# 2. Create Function Apps (Consumption Plan)
echo "Creating Function Apps..."

# Create OCR App
az functionapp create --resource-group $RESOURCE_GROUP --consumption-plan-location $LOCATION \
--runtime python --runtime-version 3.11 --functions-version 4 --name $OCR_APP_NAME --os-type linux \
--storage-account $STORAGE_ACCOUNT

# Create Worker App
az functionapp create --resource-group $RESOURCE_GROUP --consumption-plan-location $LOCATION \
--runtime python --runtime-version 3.11 --functions-version 4 --name $WORKER_APP_NAME --os-type linux \
--storage-account $STORAGE_ACCOUNT

# 3. Configure App Settings
echo "Configuring App Settings..."

# Fetch Service Bus Connection String
SB_CONN_STR=$(az servicebus namespace authorization-rule keys list --resource-group $RESOURCE_GROUP --namespace-name $SB_NAMESPACE --name RootManageSharedAccessKey --query primaryConnectionString --output tsv)

# Fetch Storage Connection String (for blobs)
STORAGE_CONN_STR=$(az storage account show-connection-string --name $STORAGE_ACCOUNT --resource-group $RESOURCE_GROUP --query connectionString --output tsv)

# Settings for OCR App
echo "Updating OCR App Settings..."
az functionapp config appsettings set --name $OCR_APP_NAME --resource-group $RESOURCE_GROUP --settings \
DOC_INGEST_QUEUE=$QUEUE_NAME \
SERVICEBUS_CONNECTION="$SB_CONN_STR" \
AzureWebJobsStorage="$STORAGE_CONN_STR" \
INCOMING_CONTAINER="incoming" \
PROCESSED_CONTAINER="processed" \
# Add other DI/AOAI keys here if needed

# Settings for Worker App
echo "Updating Worker App Settings..."
az functionapp config appsettings set --name $WORKER_APP_NAME --resource-group $RESOURCE_GROUP --settings \
DOC_INGEST_QUEUE=$QUEUE_NAME \
SERVICEBUS_CONNECTION="$SB_CONN_STR" \
AzureWebJobsStorage="$STORAGE_CONN_STR" \
INCOMING_CONTAINER="incoming" \
PROCESSED_CONTAINER="processed" \
SCOPE_XLSX_PATH="Scope_Workbook.xlsx" \
DI_SCRIPT_PATH="llm_doc_pipeline/orchestrator/ocr/di_pages_merge.py" \
# Add DB URL, DI Endpoint, AOAI keys here.

# 4. Deploy Code
echo "Deploying Code..."

echo "Deploying di-ocr..."
cd di-ocr
func azure functionapp publish $OCR_APP_NAME
cd ..

echo "Deploying doc-worker..."
cd doc-worker
func azure functionapp publish $WORKER_APP_NAME
cd ..

echo "--- Deployment Complete! ---"
echo "OCR App URL: https://$OCR_APP_NAME.azurewebsites.net"
