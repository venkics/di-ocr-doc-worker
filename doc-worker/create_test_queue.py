from azure.servicebus.management import ServiceBusAdministrationClient
import os

# Load from local.settings.json (simulated)
SB_CONN = "<YOUR_SERVICEBUS_CONNECTION_STRING>"
TEST_QUEUE_NAME = "doc-ingest-test"

from azure.core.exceptions import ResourceExistsError

def create_test_queue():
    print(f"Creating test queue: {TEST_QUEUE_NAME}...")
    admin_client = ServiceBusAdministrationClient.from_connection_string(SB_CONN)
    
    try:
        admin_client.create_queue(TEST_QUEUE_NAME)
        print(f"Successfully created queue: {TEST_QUEUE_NAME}")
    except ResourceExistsError:
        print(f"Queue {TEST_QUEUE_NAME} already exists.")
    except Exception as e:
        print(f"Failed to create queue: {e}")

if __name__ == "__main__":
    create_test_queue()
