import os
import json
from azure.servicebus import ServiceBusClient, ServiceBusMessage

# Configuration
SERVICEBUS_CONNECTION = os.environ.get("SERVICEBUS_CONNECTION")
QUEUE_NAME = "doc-ingest-af"

# Missing Doc Details
DOC_ID = "35598e71-b285-4736-b910-f3ef98edb395"
BATCH_ID = "3d0fdf7e-4488-4bc3-b66a-80f3560f4c0d"
BLOB_NAME = "3d0fdf7e-4488-4bc3-b66a-80f3560f4c0d/Goswami-Ref.pdf"
BLOB_URL = f"https://stdocpipelinev2uk.blob.core.windows.net/incoming/{BLOB_NAME}"

def main():
    if not SERVICEBUS_CONNECTION:
        print("Error: SERVICEBUS_CONNECTION env var missing")
        return

    print(f"Re-queuing doc {DOC_ID} for batch {BATCH_ID}...")
    
    msg_body = {
        "doc_id": "b5ff71b6-66bc-4f8c-9679-53a068e66b76",
        "batch_id": "1a3a6f4a-b958-4e92-89d9-3907d9a1fa74",
        "blob_name": "1a3a6f4a-b958-4e92-89d9-3907d9a1fa74/Goswami-Test.pdf",
        "blob_url": "https://stdocpipelinev2uk.blob.core.windows.net/incoming/1a3a6f4a-b958-4e92-89d9-3907d9a1fa74/Goswami-Test.pdf",
        "attempt": 1,
        "requeued": True
    }

    sb = ServiceBusClient.from_connection_string(SERVICEBUS_CONNECTION)
    with sb:
        sender = sb.get_queue_sender(queue_name=QUEUE_NAME)
        with sender:
            sender.send_messages(ServiceBusMessage(json.dumps(msg_body)))
            print("Message sent successfully.")

if __name__ == "__main__":
    main()
