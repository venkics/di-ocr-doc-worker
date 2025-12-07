import os
from azure.servicebus import ServiceBusClient, ServiceBusSubQueue

SERVICEBUS_CONNECTION = os.environ.get("SERVICEBUS_CONNECTION")
QUEUE_NAME = "doc-ingest-af"

if not SERVICEBUS_CONNECTION:
    # Fallback for local run if env var not set
    SERVICEBUS_CONNECTION = os.environ.get("SERVICEBUS_CONNECTION_STRING")
    if not SERVICEBUS_CONNECTION:
        raise ValueError("Please set SERVICEBUS_CONNECTION env var")

def clear_dlq():
    sb = ServiceBusClient.from_connection_string(SERVICEBUS_CONNECTION)
    with sb:
        receiver = sb.get_queue_receiver(queue_name=QUEUE_NAME, sub_queue=ServiceBusSubQueue.DEAD_LETTER)
        with receiver:
            messages = receiver.receive_messages(max_message_count=100, max_wait_time=5)
            print(f"Found {len(messages)} messages in DLQ.")
            for msg in messages:
                print(f"Completing message: {msg.message_id}")
                receiver.complete_message(msg)
    print("DLQ Cleared.")

if __name__ == "__main__":
    clear_dlq()
