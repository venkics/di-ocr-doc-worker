#!/bin/bash

# Usage: ./get_batch_details.sh <BATCH_ID>

if [ -z "$1" ]; then
    echo "Usage: $0 <BATCH_ID>"
    exit 1
fi

BATCH_ID="$1"

# Configuration
POSTGRES_HOST="pg-docpipeline-v2-uk.postgres.database.azure.com"
POSTGRES_USER="pgadmin"
POSTGRES_DB="docpipeline"
export PGPASSWORD="DocPipeline_2024!"

echo "--- Batch Details for ID: $BATCH_ID ---"

/opt/homebrew/opt/postgresql@14/bin/psql \
    "host=$POSTGRES_HOST user=$POSTGRES_USER dbname=$POSTGRES_DB sslmode=require" \
    -x -c "
SELECT
    b.batch_id,
    b.status as batch_status,
    b.created_at as batch_created,
    b.started_at as batch_started,
    b.finished_at as batch_finished,
    (EXTRACT(EPOCH FROM (b.finished_at - b.started_at)))::numeric(10,2) || ' seconds' as batch_duration,
    d.doc_id,
    d.filename,
    d.status as doc_status,
    d.created_at as doc_created,
    d.started_at as doc_started,
    d.finished_at as doc_finished,
    (EXTRACT(EPOCH FROM (d.finished_at - d.started_at)))::numeric(10,2) || ' seconds' as doc_duration,
    d.error
FROM
    public.batches b
LEFT JOIN
    public.documents d ON b.batch_id = d.batch_id
WHERE
    b.batch_id = '$BATCH_ID';
"
