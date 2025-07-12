import os
import sys
import json
import uuid
import datetime
import logging
import requests
from pathlib import Path

# --- Configuration ---
# The test runner gets the target URL from an environment variable set in docker-compose
TARGET_URL = 'http://localhost:5001'
LOG_DIR = '/app/logs'
REPORT_FILE = os.path.join(LOG_DIR, 'test_report.log')
TEST_IMAGE_NAME = 'test_image.jpg'

# --- Logging Setup ---
# Ensures the log directory exists inside the container
os.makedirs(LOG_DIR, exist_ok=True)

# Configures logging to both a file and the console
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] - %(message)s')
file_handler = logging.FileHandler(REPORT_FILE)
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

logger = logging.getLogger('TestRunner')
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

class TestSummary:
    """A simple class to hold test results."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.test_data = {} # To store data between tests, like plate number

    def record_pass(self, message):
        logger.info(f"✅ PASSED: {message}")
        self.passed += 1

    def record_fail(self, message):
        logger.error(f"❌ FAILED: {message}")
        self.failed += 1

summary = TestSummary()

def run_test(test_func, *args, **kwargs):
    """Decorator to run a test function and handle exceptions."""
    test_name = test_func.__name__
    logger.info(f"--- RUNNING: {test_name} ---")
    try:
        test_func(*args, **kwargs)
    except Exception as e:
        summary.record_fail(f"{test_name} - An unexpected error occurred: {e}")

# --- Test Cases ---

def test_health_endpoint():
    """Checks if the /health endpoint is responsive and correct."""
    try:
        response = requests.get(f"{TARGET_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'ok':
                summary.record_pass("/health endpoint is OK.")
            else:
                summary.record_fail(f"/health endpoint returned wrong status: {data.get('status')}")
        else:
            summary.record_fail(f"/health endpoint returned status code {response.status_code}")
    except requests.RequestException as e:
        summary.record_fail(f"Could not connect to /health endpoint: {e}")

def test_event_submission():
    """Tests submitting a new event with an image to the /event endpoint."""
    # 1. Create a dummy image file
    Path(TEST_IMAGE_NAME).touch()
    
    # 2. Prepare unique event data
    plate_number = f"TEST-{(str(uuid.uuid4())[:4])}"
    camera_id = "CAM-TEST"
    event_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    
    summary.test_data['plate_number'] = plate_number
    summary.test_data['camera_id'] = camera_id

    event_data = {
        "Timestamp": event_time,
        "EventTimeUTC": event_time,
        "PlateNumber": plate_number,
        "CameraID": camera_id,
        "Confidence": 0.95
    }

    # 3. Send the POST request
    try:
        with open(TEST_IMAGE_NAME, 'rb') as f:
            files = {'image': (TEST_IMAGE_NAME, f, 'image/jpeg')}
            payload = {'event_data': json.dumps(event_data)}
            response = requests.post(f"{TARGET_URL}/event", files=files, data=payload, timeout=10)

        if response.status_code == 201:
            data = response.json()
            if data.get('status') == 'success':
                summary.record_pass("/event submission successful.")
            else:
                summary.record_fail(f"/event submission returned wrong status: {data.get('status')}")
        else:
            summary.record_fail(f"/event submission returned status code {response.status_code}. Response: {response.text}")

    except requests.RequestException as e:
        summary.record_fail(f"Could not connect to /event endpoint: {e}")
    finally:
        # 4. Clean up the dummy image
        os.remove(TEST_IMAGE_NAME)

def test_get_events_and_filtering():
    """Tests fetching events and verifying data types and filtering."""
    plate_to_find = summary.test_data.get('plate_number')
    if not plate_to_find:
        summary.record_fail("Cannot test filtering; no plate number was stored from submission test.")
        return
        
    try:
        # Test 1: Fetch with filter to find our event
        params = {'plate_number': plate_to_find}
        response = requests.get(f"{TARGET_URL}/api/events", params=params, timeout=5)
        
        if response.status_code != 200:
            summary.record_fail(f"/api/events with filter returned status code {response.status_code}")
            return

        data = response.json()
        
        # Verify data structure and types
        assert 'events' in data and isinstance(data['events'], list), "Response missing 'events' list."
        assert 'total_events' in data and isinstance(data['total_events'], int), "Response missing 'total_events' int."
        
        if data['total_events'] == 1:
            summary.record_pass("/api/events filter found the correct number of events (1).")
            event = data['events'][0]
            if event.get('plate_number') == plate_to_find:
                 summary.record_pass("Filtered event has the correct plate number.")
            else:
                 summary.record_fail("Filtered event has the wrong plate number.")
        else:
            summary.record_fail(f"Filter for plate '{plate_to_find}' found {data['total_events']} events, expected 1.")

    except (requests.RequestException, AssertionError) as e:
        summary.record_fail(f"Error testing /api/events: {e}")


def test_get_cameras():
    """Tests the /api/cameras endpoint."""
    camera_to_find = summary.test_data.get('camera_id')
    if not camera_to_find:
        summary.record_fail("Cannot test cameras; no camera ID was stored.")
        return
        
    try:
        response = requests.get(f"{TARGET_URL}/api/cameras", timeout=5)
        if response.status_code != 200:
            summary.record_fail(f"/api/cameras returned status code {response.status_code}")
            return

        data = response.json()
        assert 'cameras' in data and isinstance(data['cameras'], list), "Response missing 'cameras' list."
        summary.record_pass("/api/cameras returned the correct data structure.")
        
        if camera_to_find in data['cameras']:
            summary.record_pass(f"Found test camera '{camera_to_find}' in the list.")
        else:
            summary.record_fail(f"Did not find test camera '{camera_to_find}' in the list.")
            
    except (requests.RequestException, AssertionError) as e:
        summary.record_fail(f"Error testing /api/cameras: {e}")

def test_get_latest_timestamp():
    """Tests the /api/events/latest_timestamp endpoint."""
    try:
        response = requests.get(f"{TARGET_URL}/api/events/latest_timestamp", timeout=5)
        if response.status_code != 200:
            summary.record_fail(f"/api/events/latest_timestamp returned status code {response.status_code}")
            return

        data = response.json()
        timestamp = data.get('latest_timestamp')
        assert timestamp is not None, "Response missing 'latest_timestamp'."
        
        # Verify it's a valid ISO 8601 timestamp string
        datetime.datetime.fromisoformat(timestamp)
        summary.record_pass("/api/events/latest_timestamp returned a valid timestamp.")
        
    except (requests.RequestException, AssertionError, ValueError) as e:
        summary.record_fail(f"Error testing latest_timestamp: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    logger.info("=============================================")
    logger.info("  STARTING ANPR_DB_MANAGER AUTOMATED TEST  ")
    logger.info("=============================================")
    
    # Run all test cases
    run_test(test_health_endpoint)
    run_test(test_event_submission)
    run_test(test_get_events_and_filtering)
    run_test(test_get_cameras)
    run_test(test_get_latest_timestamp)
    
    # Final summary
    logger.info("---------------------------------------------")
    logger.info("                 TEST SUMMARY                ")
    logger.info(f"    PASSED: {summary.passed}")
    logger.info(f"    FAILED: {summary.failed}")
    logger.info("---------------------------------------------")
    logger.info(f"Full report saved to: {REPORT_FILE}")

    # Exit with a non-zero status code if any tests failed
    if summary.failed > 0:
        sys.exit(1)