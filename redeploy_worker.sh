#!/bin/bash

# Worker App Name
WORKER_APP_NAME="doc-worker-func-app-1764481450"

echo "Redeploying Worker App ($WORKER_APP_NAME)..."
cd doc-worker
func azure functionapp publish $WORKER_APP_NAME --python
cd ..

echo "Redeployment complete!"
