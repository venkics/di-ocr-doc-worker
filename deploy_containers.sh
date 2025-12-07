#!/bin/bash
set -e

# ==============================================================================
# Deploy DI-OCR and Doc-Worker as Azure Container Apps
# 
# This script REUSES existing resources from deploy_prod_v2.sh:
#   - Storage Account (stdocpipelinev2uk)
#   - Service Bus (sb-docpipeline-v2-uk)
#   - Document Intelligence (di-docpipeline-v2-uk)
#   - Azure OpenAI (aoai-docpipeline-v2-uk)
#
# This script CREATES new resources:
#   - Azure Container Registry (acrdocpipelinev2uk)
#   - Azure Database for PostgreSQL Flexible Server (pg-docpipeline-v2-uk)
#   - Container Apps Environment (cae-docpipeline-v2-uk)
#   - Container App for di-ocr (ca-di-ocr-v2)
#   - Container App for doc-worker (ca-doc-worker-v2)
# ==============================================================================

# --- Configuration (from deploy_prod_v2.sh) ---
SUBSCRIPTION_ID="b41c6574-a23d-4540-986f-83455c066bc8"
RESOURCE_GROUP="rg-docpipeline-prod-v2"
LOCATION="uksouth"
AOAI_LOCATION="eastus"

# EXISTING Resources (from deploy_prod_v2.sh)
STORAGE_ACCOUNT_NAME="stdocpipelinev2uk"
INCOMING_CONTAINER="incoming"
PROCESSED_CONTAINER="processed"
SB_NAMESPACE="sb-docpipeline-v2-uk"
QUEUE_NAME="doc-ingest-af"
DI_SERVICE_NAME="di-docpipeline-v2-uk"
AOAI_SERVICE_NAME="aoai-docpipeline-v2-uk"
AOAI_DEPLOYMENT="gpt-4.1"
AOAI_API_VERSION="2024-06-01"

# NEW Resources (Container Infrastructure)
ACR_NAME="acrdocpipelinev2uk"
POSTGRES_SERVER_NAME="pg-docpipeline-v2-uk"
POSTGRES_DB_NAME="docpipeline"
POSTGRES_ADMIN_USER="pgadmin"
POSTGRES_ADMIN_PASSWORD="DocPipeline_2024!"  # Change this in production!
CAE_NAME="cae-docpipeline-v2-uk"
CA_DI_OCR_NAME="ca-di-ocr-v2"
CA_DOC_WORKER_NAME="ca-doc-worker-v2"

# --- Set Subscription ---
echo "--- Setting Azure Subscription ---"
az account set --subscription $SUBSCRIPTION_ID

# ==============================================================================
# STEP 1: Retrieve EXISTING Resource Credentials
# ==============================================================================
echo ""
echo "--- 1. Retrieving EXISTING Resource Credentials ---"

# Storage Account Key
echo "   > Getting Storage Account Key..."
STORAGE_KEY=$(az storage account keys list \
    --resource-group $RESOURCE_GROUP \
    --account-name $STORAGE_ACCOUNT_NAME \
    --query "[0].value" -o tsv)

if [ -z "$STORAGE_KEY" ]; then
    echo "ERROR: Could not retrieve Storage Account Key. Ensure $STORAGE_ACCOUNT_NAME exists."
    exit 1
fi
echo "     Storage Account Key: retrieved"

# Service Bus Connection String
echo "   > Getting Service Bus Connection String..."
SB_CONN_STR=$(az servicebus namespace authorization-rule keys list \
    --resource-group $RESOURCE_GROUP \
    --namespace-name $SB_NAMESPACE \
    --name RootManageSharedAccessKey \
    --query primaryConnectionString -o tsv)

if [ -z "$SB_CONN_STR" ]; then
    echo "ERROR: Could not retrieve Service Bus Connection String. Ensure $SB_NAMESPACE exists."
    exit 1
fi
echo "     Service Bus Connection String: retrieved"

# Document Intelligence
echo "   > Getting Document Intelligence Credentials..."
DI_ENDPOINT=$(az cognitiveservices account show \
    --name $DI_SERVICE_NAME \
    --resource-group $RESOURCE_GROUP \
    --query properties.endpoint -o tsv)
DI_KEY=$(az cognitiveservices account keys list \
    --name $DI_SERVICE_NAME \
    --resource-group $RESOURCE_GROUP \
    --query key1 -o tsv)

if [ -z "$DI_ENDPOINT" ] || [ -z "$DI_KEY" ]; then
    echo "ERROR: Could not retrieve Document Intelligence credentials. Ensure $DI_SERVICE_NAME exists."
    exit 1
fi
echo "     Document Intelligence Endpoint: $DI_ENDPOINT"

# Azure OpenAI
echo "   > Getting Azure OpenAI Credentials..."
AOAI_ENDPOINT=$(az cognitiveservices account show \
    --name $AOAI_SERVICE_NAME \
    --resource-group $RESOURCE_GROUP \
    --query properties.endpoint -o tsv)
AOAI_KEY=$(az cognitiveservices account keys list \
    --name $AOAI_SERVICE_NAME \
    --resource-group $RESOURCE_GROUP \
    --query key1 -o tsv)

if [ -z "$AOAI_ENDPOINT" ] || [ -z "$AOAI_KEY" ]; then
    echo "ERROR: Could not retrieve Azure OpenAI credentials. Ensure $AOAI_SERVICE_NAME exists."
    exit 1
fi
echo "     Azure OpenAI Endpoint: $AOAI_ENDPOINT"

echo "   ✓ All existing resource credentials retrieved successfully"

# ==============================================================================
# STEP 2: Create Azure Container Registry
# ==============================================================================
echo ""
echo "--- 2. Creating Azure Container Registry ($ACR_NAME) ---"

# Check if ACR already exists
ACR_EXISTS=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query name -o tsv 2>/dev/null || echo "")

if [ -z "$ACR_EXISTS" ]; then
    az acr create \
        --resource-group $RESOURCE_GROUP \
        --name $ACR_NAME \
        --sku Basic \
        --location $LOCATION \
        --admin-enabled true
    echo "   ✓ ACR created"
else
    echo "   ✓ ACR already exists, skipping creation"
fi

# Get ACR credentials
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query "passwords[0].value" -o tsv)

echo "   ACR Login Server: $ACR_LOGIN_SERVER"

# ==============================================================================
# STEP 3: Create Azure Database for PostgreSQL Flexible Server
# ==============================================================================
echo ""
echo "--- 3. Creating Azure Database for PostgreSQL ($POSTGRES_SERVER_NAME) ---"

# Check if PostgreSQL server already exists
PG_EXISTS=$(az postgres flexible-server show --name $POSTGRES_SERVER_NAME --resource-group $RESOURCE_GROUP --query name -o tsv 2>/dev/null || echo "")

if [ -z "$PG_EXISTS" ]; then
    az postgres flexible-server create \
        --resource-group $RESOURCE_GROUP \
        --name $POSTGRES_SERVER_NAME \
        --location $LOCATION \
        --admin-user $POSTGRES_ADMIN_USER \
        --admin-password "$POSTGRES_ADMIN_PASSWORD" \
        --sku-name Standard_B1ms \
        --tier Burstable \
        --storage-size 32 \
        --version 16 \
        --public-access 0.0.0.0-255.255.255.255  # Allow Azure services
    
    echo "   > Waiting 60s for PostgreSQL server to be ready..."
    sleep 60
    echo "   ✓ PostgreSQL server created"
else
    echo "   ✓ PostgreSQL server already exists, skipping creation"
fi

# Create database
echo "   > Creating database ($POSTGRES_DB_NAME)..."
az postgres flexible-server db create \
    --resource-group $RESOURCE_GROUP \
    --server-name $POSTGRES_SERVER_NAME \
    --database-name $POSTGRES_DB_NAME \
    2>/dev/null || echo "     Database may already exist, continuing..."

# Build PostgreSQL connection URL
POSTGRES_HOST="${POSTGRES_SERVER_NAME}.postgres.database.azure.com"
POSTGRES_URL="postgresql://${POSTGRES_ADMIN_USER}:${POSTGRES_ADMIN_PASSWORD}@${POSTGRES_HOST}:5432/${POSTGRES_DB_NAME}?sslmode=require"

echo "   PostgreSQL Host: $POSTGRES_HOST"

# ==============================================================================
# STEP 4: Initialize PostgreSQL Schema
# ==============================================================================
echo ""
echo "--- 4. Initializing PostgreSQL Schema ---"

# Create schema SQL
SCHEMA_SQL="
CREATE TABLE IF NOT EXISTS public.batches (
  batch_id   UUID PRIMARY KEY,
  patient_id TEXT,
  status     TEXT NOT NULL DEFAULT 'queued',
  created_at TIMESTAMPTZ DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.documents (
  doc_id     UUID PRIMARY KEY,
  batch_id   UUID REFERENCES public.batches(batch_id) ON DELETE CASCADE,
  blob_name  TEXT,
  blob_url   TEXT,
  filename   TEXT,
  status     TEXT DEFAULT 'queued',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error      TEXT,
  final_json_url TEXT,
  final_graph_xlsx_url TEXT,
  graph_pages_prefix_url TEXT,
  graph_xlsx_url TEXT
);
"

# Execute schema via psql (requires psql client)
echo "   > Attempting to initialize schema..."
PGPASSWORD="$POSTGRES_ADMIN_PASSWORD" psql \
    -h "$POSTGRES_HOST" \
    -U "$POSTGRES_ADMIN_USER" \
    -d "$POSTGRES_DB_NAME" \
    -c "$SCHEMA_SQL" \
    2>/dev/null && echo "   ✓ Schema initialized" || echo "   ! Schema will be auto-created by di-ocr on startup"

# ==============================================================================
# STEP 5: Build and Push Container Images
# ==============================================================================
echo ""
echo "--- 5. Building and Pushing Container Images ---"

# Login to ACR
echo "   > Logging into ACR..."
az acr login --name $ACR_NAME

# Build and push di-ocr
echo "   > Building di-ocr image..."
cd di-ocr
docker build --platform linux/amd64 -t $ACR_LOGIN_SERVER/di-ocr:latest .
docker push $ACR_LOGIN_SERVER/di-ocr:latest
cd ..
echo "   ✓ di-ocr image pushed"

# Build and push doc-worker
echo "   > Building doc-worker image..."
cd doc-worker
docker build --platform linux/amd64 --no-cache -t $ACR_LOGIN_SERVER/doc-worker:v8 .
docker push $ACR_LOGIN_SERVER/doc-worker:v8
cd ..
echo "   ✓ doc-worker image pushed"

# ==============================================================================
# STEP 6: Create Container Apps Environment
# ==============================================================================
echo ""
echo "--- 6. Creating Container Apps Environment ($CAE_NAME) ---"

# Check if environment already exists
CAE_EXISTS=$(az containerapp env show --name $CAE_NAME --resource-group $RESOURCE_GROUP --query name -o tsv 2>/dev/null || echo "")

if [ -z "$CAE_EXISTS" ]; then
    az containerapp env create \
        --name $CAE_NAME \
        --resource-group $RESOURCE_GROUP \
        --location $LOCATION
    echo "   ✓ Container Apps Environment created"
else
    echo "   ✓ Container Apps Environment already exists, skipping creation"
fi

# ==============================================================================
# STEP 7: Deploy Container Apps
# ==============================================================================
echo ""
echo "--- 7. Deploying Container Apps ---"

# Deploy di-ocr Container App
echo "   > Deploying di-ocr Container App ($CA_DI_OCR_NAME)..."
az containerapp create \
    --name $CA_DI_OCR_NAME \
    --resource-group $RESOURCE_GROUP \
    --environment $CAE_NAME \
    --image $ACR_LOGIN_SERVER/di-ocr:latest \
    --registry-server $ACR_LOGIN_SERVER \
    --registry-username $ACR_USERNAME \
    --registry-password $ACR_PASSWORD \
    --target-port 80 \
    --ingress external \
    --min-replicas 1 \
    --max-replicas 3 \
    --cpu 0.5 \
    --memory 1.0Gi \
    --env-vars \
        POSTGRES_URL="$POSTGRES_URL" \
        STORAGE_ACCOUNT_NAME="$STORAGE_ACCOUNT_NAME" \
        STORAGE_ACCOUNT_KEY="$STORAGE_KEY" \
        SERVICEBUS_CONNECTION="$SB_CONN_STR" \
        INCOMING_CONTAINER="$INCOMING_CONTAINER" \
        PROCESSED_CONTAINER="$PROCESSED_CONTAINER" \
        DOC_INGEST_QUEUE="$QUEUE_NAME"

DI_OCR_URL=$(az containerapp show --name $CA_DI_OCR_NAME --resource-group $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv)
echo "   ✓ di-ocr deployed: https://$DI_OCR_URL"

# Deploy doc-worker Container App
echo "   > Deploying doc-worker Container App ($CA_DOC_WORKER_NAME)..."
az containerapp create \
    --name $CA_DOC_WORKER_NAME \
    --resource-group $RESOURCE_GROUP \
    --environment $CAE_NAME \
    --image $ACR_LOGIN_SERVER/doc-worker:v8 \
    --registry-server $ACR_LOGIN_SERVER \
    --registry-username $ACR_USERNAME \
    --registry-password $ACR_PASSWORD \
    --min-replicas 2 \
    --max-replicas 5 \
    --cpu 1.0 \
    --memory 2.0Gi \
    --env-vars \
        POSTGRES_URL="$POSTGRES_URL" \
        STORAGE_ACCOUNT_NAME="$STORAGE_ACCOUNT_NAME" \
        STORAGE_ACCOUNT_KEY="$STORAGE_KEY" \
        SERVICEBUS_CONNECTION="$SB_CONN_STR" \
        QUEUE_NAME="$QUEUE_NAME" \
        INCOMING_CONTAINER="$INCOMING_CONTAINER" \
        PROCESSED_CONTAINER="$PROCESSED_CONTAINER" \
        DI_ENDPOINT="$DI_ENDPOINT" \
        DI_KEY="$DI_KEY" \
        AOAI_ENDPOINT="$AOAI_ENDPOINT" \
        AOAI_API_KEY="$AOAI_KEY" \
        AOAI_API_VERSION="$AOAI_API_VERSION" \
        AOAI_CHAT_DEPLOYMENT="$AOAI_DEPLOYMENT" \
        DI_SCRIPT_PATH="/app/llm_doc_pipeline/orchestrator/ocr/di_pages_merge.py" \
        SCOPE_XLSX_PATH="/app/assets/Scope_Workbook.xlsx" \
        SB_PREFETCH="1" \
        SB_MAX_WAIT_SECONDS="20" \
        SB_LOCK_RENEW_SECS="900" \
        PAGE_PARALLEL="3" \
        DI_MAX_INFLIGHT="3" \
        FIRST_PAGE_BURST="2" \
        DI_API_VERSION="2023-07-31" \
        PYTHONUNBUFFERED="1"

echo "   ✓ doc-worker deployed"

# ==============================================================================
# DEPLOYMENT COMPLETE
# ==============================================================================
echo ""
echo "=============================================="
echo "   CONTAINER DEPLOYMENT COMPLETE!"
echo "=============================================="
echo ""
echo "NEW Resources Created:"
echo "  - Container Registry: $ACR_LOGIN_SERVER"
echo "  - PostgreSQL Server:  $POSTGRES_HOST"
echo "  - Container Apps Env: $CAE_NAME"
echo "  - di-ocr App:         https://$DI_OCR_URL"
echo "  - doc-worker App:     $CA_DOC_WORKER_NAME (background worker, no ingress)"
echo ""
echo "EXISTING Resources Used:"
echo "  - Storage Account:    $STORAGE_ACCOUNT_NAME"
echo "  - Service Bus:        $SB_NAMESPACE"
echo "  - Document Intel:     $DI_SERVICE_NAME"
echo "  - Azure OpenAI:       $AOAI_SERVICE_NAME"
echo ""
echo "Test the API:"
echo "  curl https://$DI_OCR_URL/healthz"
echo ""
