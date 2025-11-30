import os
from azure.servicebus import ServiceBusClient, ServiceBusSubQueue
from azure.servicebus.management import ServiceBusAdministrationClient

# Load from local.settings.json (simulated)
SB_CONN = "<YOUR_SERVICEBUS_CONNECTION_STRING>"
QUEUE_NAME = "doc-ingest"

def check_queue():
    print(f"Checking queue: {QUEUE_NAME}...")
    
    # 1. Check Active Message Count
    admin_client = ServiceBusAdministrationClient.from_connection_string(SB_CONN)
    props = admin_client.get_queue_runtime_properties(QUEUE_NAME)
    
    print(f"Active Messages: {props.active_message_count}")
    print(f"Dead Letter Messages: {props.dead_letter_message_count}")
    
    # 2. Peek at the first message if exists
    if props.active_message_count > 0:
        sb_client = ServiceBusClient.from_connection_string(SB_CONN)
        with sb_client:
            receiver = sb_client.get_queue_receiver(queue_name=QUEUE_NAME)
            with receiver:
                msgs = receiver.peek_messages(max_message_count=1)
                if msgs:
                    print(f"Peeked Active Message ID: {msgs[0].message_id}")
                    print(f"Body: {msgs[0].body}")

    # 3. Peek at DLQ
    if props.dead_letter_message_count > 0:
        print("\n--- Peeking Dead Letter Queue ---")
        sb_client = ServiceBusClient.from_connection_string(SB_CONN)
        with sb_client:
            receiver = sb_client.get_queue_receiver(queue_name=QUEUE_NAME, sub_queue=ServiceBusSubQueue.DEAD_LETTER)
            with receiver:
                msgs = receiver.peek_messages(max_message_count=1)
                if msgs:
                    print(f"Peeked DLQ Message ID: {msgs[0].message_id}")
                    print(f"Reason: {msgs[0].application_properties.get(b'DeadLetterReason', 'Unknown')}")
                    print(f"Description: {msgs[0].application_properties.get(b'DeadLetterErrorDescription', 'Unknown')}")
                    print(f"Body: {msgs[0].body}")

if __name__ == "__main__":
    check_queue()
