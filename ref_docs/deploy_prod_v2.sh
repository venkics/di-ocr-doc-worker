#!/bin/bash
set -e

# --- Configuration ---
SUBSCRIPTION_ID="b41c6574-a23d-4540-986f-83455c066bc8"
RESOURCE_GROUP="rg-docpipeline-prod-v2" # NEW Resource Group
LOCATION="uksouth"
AOAI_LOCATION="eastus"

# Unique Resource Names (V2)
STORAGE_ACCOUNT_NAME="stdocpipelinev2uk" 
INCOMING_CONTAINER="incoming"
PROCESSED_CONTAINER="processed"

# Service Bus
SB_NAMESPACE="sb-docpipeline-v2-uk"
QUEUE_NAME="doc-ingest-af"

# AI Services
DI_SERVICE_NAME="di-docpipeline-v2-uk"
AOAI_SERVICE_NAME="aoai-docpipeline-v2-uk"
AOAI_DEPLOYMENT="gpt-4.1"

# Function Apps
OCR_APP_NAME="di-ocr-prod-v2"
WORKER_APP_NAME="doc-worker-prod-v2"

# SQL Server (Existing)
SQL_CONN_STR="Driver={ODBC Driver 18 for SQL Server};Server=tcp:techlotussqlserver-dev.database.windows.net,1433;Database=techlotusstgdb;Uid=techlotusdev;Pwd=Deepak_30;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"

# --- Execution ---

echo "--- 1. Creating Resource Group ($RESOURCE_GROUP) ---"
az group create --name $RESOURCE_GROUP --location $LOCATION

echo "--- 2. Creating Storage Account ($STORAGE_ACCOUNT_NAME) ---"
az storage account create --name $STORAGE_ACCOUNT_NAME --resource-group $RESOURCE_GROUP --location $LOCATION --sku Standard_LRS
echo "   > Waiting 30s for Storage propagation..."
sleep 30

# Get Key
STORAGE_KEY=$(az storage account keys list --resource-group $RESOURCE_GROUP --account-name $STORAGE_ACCOUNT_NAME --query "[0].value" -o tsv)

# Create Containers
echo "   > Creating containers..."
az storage container create --name $INCOMING_CONTAINER --account-name $STORAGE_ACCOUNT_NAME --account-key $STORAGE_KEY
az storage container create --name $PROCESSED_CONTAINER --account-name $STORAGE_ACCOUNT_NAME --account-key $STORAGE_KEY

echo "--- 3. Creating Service Bus ($SB_NAMESPACE) ---"
az servicebus namespace create --resource-group $RESOURCE_GROUP --name $SB_NAMESPACE --location $LOCATION --sku Standard
echo "   > Waiting 60s for Service Bus DNS propagation..."
sleep 60

echo "   > Creating Queue..."
az servicebus queue create --resource-group $RESOURCE_GROUP --namespace-name $SB_NAMESPACE --name $QUEUE_NAME

echo "   > Getting Connection String..."
# Retry logic for connection string
for i in {1..5}; do
   SB_CONN_STR=$(az servicebus namespace authorization-rule keys list --resource-group $RESOURCE_GROUP --namespace-name $SB_NAMESPACE --name RootManageSharedAccessKey --query primaryConnectionString -o tsv)
   if [ -n "$SB_CONN_STR" ]; then
      break
   fi
   echo "     Retry $i: Waiting for auth rule..."
   sleep 10
done

if [ -z "$SB_CONN_STR" ]; then
    echo "ERROR: Could not retrieve Service Bus Connection String."
    exit 1
fi

echo "--- 4. Creating AI Services ---"
# Document Intelligence
echo "   > Document Intelligence ($DI_SERVICE_NAME)..."
az cognitiveservices account create --name $DI_SERVICE_NAME --resource-group $RESOURCE_GROUP --kind FormRecognizer --sku S0 --location $LOCATION --yes
DI_ENDPOINT=$(az cognitiveservices account show --name $DI_SERVICE_NAME --resource-group $RESOURCE_GROUP --query properties.endpoint -o tsv)
DI_KEY=$(az cognitiveservices account keys list --name $DI_SERVICE_NAME --resource-group $RESOURCE_GROUP --query key1 -o tsv)

# Azure OpenAI
echo "   > Azure OpenAI ($AOAI_SERVICE_NAME) in $AOAI_LOCATION..."
az cognitiveservices account create --name $AOAI_SERVICE_NAME --resource-group $RESOURCE_GROUP --kind OpenAI --sku S0 --location $AOAI_LOCATION --yes
echo "   > Waiting 20s for AOAI propagation..."
sleep 20

AOAI_ENDPOINT=$(az cognitiveservices account show --name $AOAI_SERVICE_NAME --resource-group $RESOURCE_GROUP --query properties.endpoint -o tsv)
AOAI_KEY=$(az cognitiveservices account keys list --name $AOAI_SERVICE_NAME --resource-group $RESOURCE_GROUP --query key1 -o tsv)

# Deploy Model
echo "   > Deploying Model ($AOAI_DEPLOYMENT)..."
# Check if deployment exists first to avoid error
EXISTS=$(az cognitiveservices account deployment list --name $AOAI_SERVICE_NAME --resource-group $RESOURCE_GROUP --query "[?name=='$AOAI_DEPLOYMENT']" -o tsv)
if [ -z "$EXISTS" ]; then
    az cognitiveservices account deployment create \
    --resource-group $RESOURCE_GROUP \
    --name $AOAI_SERVICE_NAME \
    --deployment-name $AOAI_DEPLOYMENT \
    --model-name gpt-4o \
    --model-version "2024-05-13" \
    --model-format OpenAI \
    --sku-capacity 10 --sku-name Standard
else
    echo "     Model already deployed."
fi

echo "--- 5. Creating Function Apps ---"

# DI-OCR App
echo "   > Creating DI-OCR App ($OCR_APP_NAME)..."
az functionapp create --resource-group $RESOURCE_GROUP --consumption-plan-location $LOCATION \
    --runtime python --runtime-version 3.11 --functions-version 4 --name $OCR_APP_NAME \
    --storage-account $STORAGE_ACCOUNT_NAME --os-type Linux

# Doc-Worker App
echo "   > Creating Doc-Worker App ($WORKER_APP_NAME)..."
az functionapp create --resource-group $RESOURCE_GROUP --consumption-plan-location $LOCATION \
    --runtime python --runtime-version 3.11 --functions-version 4 --name $WORKER_APP_NAME \
    --storage-account $STORAGE_ACCOUNT_NAME --os-type Linux

echo "--- 6. Configuring App Settings ---"

echo "   > Configuring DI-OCR..."
az functionapp config appsettings set --name $OCR_APP_NAME --resource-group $RESOURCE_GROUP --settings \
    SQL_CONN_STR="$SQL_CONN_STR" \
    STORAGE_ACCOUNT_NAME="$STORAGE_ACCOUNT_NAME" \
    STORAGE_ACCOUNT_KEY="$STORAGE_KEY" \
    SERVICEBUS_CONNECTION="$SB_CONN_STR" \
    INCOMING_CONTAINER="$INCOMING_CONTAINER" \
    DOC_INGEST_QUEUE="$QUEUE_NAME" \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true

echo "   > Configuring Doc-Worker..."
az functionapp config appsettings set --name $WORKER_APP_NAME --resource-group $RESOURCE_GROUP --settings \
    SQL_CONN_STR="$SQL_CONN_STR" \
    STORAGE_ACCOUNT_NAME="$STORAGE_ACCOUNT_NAME" \
    STORAGE_ACCOUNT_KEY="$STORAGE_KEY" \
    SERVICEBUS_CONNECTION="$SB_CONN_STR" \
    DI_ENDPOINT="$DI_ENDPOINT" \
    DI_KEY="$DI_KEY" \
    AOAI_ENDPOINT="$AOAI_ENDPOINT" \
    AOAI_API_KEY="$AOAI_KEY" \
    AOAI_API_VERSION="2024-06-01" \
    AOAI_CHAT_DEPLOYMENT="$AOAI_DEPLOYMENT" \
    DOC_INGEST_QUEUE="$QUEUE_NAME" \
    INCOMING_CONTAINER="$INCOMING_CONTAINER" \
    PROCESSED_CONTAINER="$PROCESSED_CONTAINER" \
    DI_SCRIPT_PATH="llm_doc_pipeline/orchestrator/ocr/di_pages_merge.py" \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true

echo "--- 7. Deploying Code ---"

echo "   > Deploying DI-OCR..."
cd di-ocr
func azure functionapp publish $OCR_APP_NAME --python
cd ..

echo "   > Deploying Doc-Worker..."
cd doc-worker
func azure functionapp publish $WORKER_APP_NAME --python
cd ..

echo "--- DEPLOYMENT V2 COMPLETE! ---"
echo "API URL: https://$OCR_APP_NAME.azurewebsites.net"
