# main.py  (doc-api / di-ocr)
import os, uuid, json, datetime as dt
from typing import Optional
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
import psycopg
from azure.storage.blob import (
    generate_container_sas, ContainerSasPermissions,
    BlobServiceClient
)
from azure.servicebus import ServiceBusClient, ServiceBusMessage

# ========= env =========
POSTGRES_URL = os.environ["POSTGRES_URL"]
STG_ACCOUNT  = os.environ["STORAGE_ACCOUNT_NAME"]
STG_KEY      = os.environ["STORAGE_ACCOUNT_KEY"]
SERVICEBUS_CONNECTION = os.environ["SERVICEBUS_CONNECTION"]

INCOMING_CONTAINER  = "incoming"
PROCESSED_CONTAINER = os.getenv("PROCESSED_CONTAINER", "processed")
DOC_QUEUE = os.getenv("DOC_INGEST_QUEUE", "doc-ingest")

# ========= helpers =========
def get_conn():
    return psycopg.connect(POSTGRES_URL)

def ensure_schema():
    """Create/patch tables & columns used by the API/worker (idempotent)."""
    with get_conn() as conn, conn.cursor() as cur:
        # batches
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.batches (
              batch_id   UUID PRIMARY KEY,
              patient_id TEXT,
              status     TEXT NOT NULL DEFAULT 'queued',
              created_at TIMESTAMPTZ DEFAULT now(),
              started_at TIMESTAMPTZ,
              finished_at TIMESTAMPTZ
            );
        """)
        # documents (base)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.documents (
              doc_id     UUID PRIMARY KEY,
              batch_id   UUID REFERENCES public.batches(batch_id) ON DELETE CASCADE
            );
        """)
        # columns used by API/worker (add-if-missing)
        cur.execute("""ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS blob_name  TEXT;""")
        cur.execute("""ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS blob_url   TEXT;""")
        cur.execute("""ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS filename   TEXT;""")
        cur.execute("""ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS status     TEXT;""")
        cur.execute("""ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();""")
        cur.execute("""ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;""")
        # defaults / backfill
        cur.execute("""ALTER TABLE public.documents ALTER COLUMN status SET DEFAULT 'queued';""")
        cur.execute("""UPDATE public.documents SET status='queued' WHERE status IS NULL;""")

def list_batch_blobs(batch_id: str):
    """Return (blob_name, blob_url) under <batch_id>/ in the 'incoming' container."""
    svc = BlobServiceClient(
        f"https://{STG_ACCOUNT}.blob.core.windows.net",
        credential=STG_KEY
    )
    cont = svc.get_container_client(INCOMING_CONTAINER)
    prefix = f"{batch_id}/"
    items = []
    for b in cont.list_blobs(name_starts_with=prefix):
        blob_url = f"https://{STG_ACCOUNT}.blob.core.windows.net/{INCOMING_CONTAINER}/{b.name}"
        items.append((b.name, blob_url))
    return items

def enqueue_docs(messages: list[dict]):
    sb = ServiceBusClient.from_connection_string(SERVICEBUS_CONNECTION, logging_enable=False)
    with sb:
        sender = sb.get_queue_sender(queue_name=DOC_QUEUE)
        with sender:
            sender.send_messages([ServiceBusMessage(json.dumps(m)) for m in messages])

def _try_read_consolidated(batch_id: str):
    """
    Try to read processed/batch/<batch_id>/Consolidated_final.json from Blob.
    Return parsed dict or None if not present yet.
    """
    svc = BlobServiceClient(
        f"https://{STG_ACCOUNT}.blob.core.windows.net",
        credential=STG_KEY
    )
    blob_path = f"batch/{batch_id}/Consolidated_final.json"
    bc = svc.get_blob_client(container=PROCESSED_CONTAINER, blob=blob_path)
    if not bc.exists():
        return None
    data = bc.download_blob().readall()
    return json.loads(data)

# ========= api =========
app = FastAPI(title="doc-api", version="0.7")

@app.on_event("startup")
def on_startup():
    ensure_schema()

class NewBatchReq(BaseModel):
    patient_id: Optional[str] = None
    sas_ttl_minutes: int = 30

class NewBatchResp(BaseModel):
    batch_id: str
    upload: dict
    status: str

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/v1/batches", response_model=NewBatchResp)
def create_batch(body: NewBatchReq):
    batch_id = str(uuid.uuid4())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.batches (batch_id, patient_id, status) VALUES (%s, %s, 'queued')",
            (batch_id, body.patient_id),
        )
    expiry = dt.datetime.utcnow() + dt.timedelta(minutes=body.sas_ttl_minutes)
    perms = ContainerSasPermissions(read=True, write=True, create=True, add=True, list=True)
    sas = generate_container_sas(
        account_name=STG_ACCOUNT,
        container_name=INCOMING_CONTAINER,
        account_key=STG_KEY,
        permission=perms,
        expiry=expiry,
        protocol="https"
    )
    upload_url = f"https://{STG_ACCOUNT}.blob.core.windows.net/{INCOMING_CONTAINER}?{sas}"
    required_prefix = f"{batch_id}/"
    return {
        "batch_id": batch_id,
        "status": "queued",
        "upload": {
            "sas_url": upload_url,
            "required_path_prefix": required_prefix,
            "expires_at_utc": expiry.isoformat() + "Z",
        },
    }

@app.get("/v1/batches/{batch_id}")
def get_batch(batch_id: str, response: Response, mode: Optional[str] = None):
    """
    Default: if all docs succeeded, return Consolidated_final.json (200).
    Otherwise 202 + progress + Retry-After: 5.

    Pass ?mode=meta to force the original metadata response for consoles.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, created_at, started_at, finished_at FROM public.batches WHERE batch_id=%s",
            (batch_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="batch not found")
        status, created_at, started_at, finished_at = row

        cur.execute(
            "SELECT status, COUNT(*) FROM public.documents WHERE batch_id=%s GROUP BY status",
            (batch_id,)
        )
        counts = {r[0]: int(r[1]) for r in cur.fetchall()}

    total = sum(counts.values()) if counts else 0
    succ  = counts.get("succeeded", 0)
    failed= counts.get("failed", 0)
    running = counts.get("running", 0)
    queued  = counts.get("queued", 0)

    if mode == "meta":
        # Original behavior for UIs that expect metadata
        return {
            "batch_id": batch_id,
            "status": status,
            "created_at": created_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "doc_counts": counts,
        }

    # Ready → try to return the consolidated JSON
    if total > 0 and succ == total and failed == 0:
        payload = _try_read_consolidated(batch_id)
        if payload is not None:
            return payload  # 200 OK with the JSON body

        # Rare race: DB shows done but blob not readable yet
        response.status_code = 202
        response.headers["Retry-After"] = "5"
        return {
            "batch_id": batch_id,
            "status": "running",
            "progress": {
                "total": total, "succeeded": succ, "failed": failed,
                "running": running, "queued": queued
            },
            "message": "Consolidated file not readable yet. Please poll again."
        }

    # Not ready → progress + 202
    response.status_code = 202
    response.headers["Retry-After"] = "5"
    return {
        "batch_id": batch_id,
        "status": status,
        "progress": {
            "total": total, "succeeded": succ, "failed": failed,
            "running": running, "queued": queued
        },
        "message": "Consolidated report not ready yet. Please poll again."
    }

@app.post("/v1/batches/{batch_id}/finalize")
def finalize(batch_id: str):
    # 1) find blobs for this batch
    blobs = list_batch_blobs(batch_id)
    if not blobs:
        raise HTTPException(status_code=400, detail=f"no files found under {batch_id}/")

    # 2) insert document rows (including filename if the column exists) + set batch running
    now = dt.datetime.utcnow()
    docs = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE public.batches SET status='running', started_at=COALESCE(started_at, %s) WHERE batch_id=%s",
            (now, batch_id),
        )

        # check if 'filename' column exists so we can populate it
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema='public' AND table_name='documents' AND column_name='filename'
        """)
        has_filename = cur.fetchone()[0] > 0

        for blob_name, blob_url in blobs:
            doc_id = str(uuid.uuid4())
            if has_filename:
                cur.execute("""
                    INSERT INTO public.documents (doc_id, batch_id, blob_name, blob_url, status, filename, created_at)
                    VALUES (%s, %s, %s, %s, 'queued', %s, now())
                """, (doc_id, batch_id, blob_name, blob_url, blob_name.split('/')[-1]))
            else:
                cur.execute("""
                    INSERT INTO public.documents (doc_id, batch_id, blob_name, blob_url, status, created_at)
                    VALUES (%s, %s, %s, %s, 'queued', now())
                """, (doc_id, batch_id, blob_name, blob_url))
            docs.append({"doc_id": doc_id, "blob_name": blob_name, "blob_url": blob_url})

    # 3) enqueue one message per doc (includes doc_id)
    msgs = [{
        "doc_id": d["doc_id"],
        "batch_id": batch_id,
        "blob_name": d["blob_name"],
        "blob_url": d["blob_url"],
        "enqueued_at": dt.datetime.utcnow().isoformat() + "Z",
        "attempt": 0
    } for d in docs]
    enqueue_docs(msgs)

    return {"batch_id": batch_id, "enqueued": len(msgs), "queue": DOC_QUEUE}
