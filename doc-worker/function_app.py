import azure.functions as func
import logging
import json
import os
import psycopg
from psycopg.rows import dict_row
from azure.storage.blob import BlobServiceClient
from doc_processor import process_message

app = func.FunctionApp()

# Global clients to reuse connections across invocations
_CONN_STR = os.environ["POSTGRES_URL"]
_STG_ACCOUNT = os.environ["STORAGE_ACCOUNT_NAME"]
_STG_KEY = os.environ["STORAGE_ACCOUNT_KEY"]
_BLOB_SERVICE = None

def get_blob_service():
    global _BLOB_SERVICE
    if _BLOB_SERVICE is None:
        _BLOB_SERVICE = BlobServiceClient(
            account_url=f"https://{_STG_ACCOUNT}.blob.core.windows.net",
            credential=_STG_KEY,
        )
    return _BLOB_SERVICE

@app.service_bus_queue_trigger(arg_name="msg", queue_name="%DOC_INGEST_QUEUE%", connection="SERVICEBUS_CONNECTION")
def doc_ingest_trigger(msg: func.ServiceBusMessage):
    try:
        print(f"!!! TRIGGERED !!! Message ID: {msg.message_id}")
        logging.info(f"Python ServiceBus queue trigger processed message: {msg.message_id}")
        
        try:
            body_str = msg.get_body().decode("utf-8")
            body = json.loads(body_str)
        except Exception as e:
            logging.error(f"Failed to decode message body: {e}")
            raise  # Retry or dead-letter

        bs = get_blob_service()
        
        # Create a new DB connection per invocation (safer for serverless concurrency)
        # or use a pool if high throughput is expected. For now, simple connect is fine.
        try:
            with psycopg.connect(_CONN_STR, autocommit=True, row_factory=dict_row) as conn:
                success = process_message(conn, bs, body)
                if not success:
                    raise Exception("Processing failed (logic returned False)")
        except Exception as e:
            logging.error(f"Error processing message: {e}")
            raise # Triggers retry
    except Exception as critical_e:
        print(f"CRITICAL WORKER FAILURE: {critical_e}")
        import traceback
        traceback.print_exc()
        raise
