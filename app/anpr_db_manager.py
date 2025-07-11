import time, sys, logging, os, re, configparser, uuid
import mysql.connector
from flask import Flask, jsonify, request # Added Flask imports

# --- Flask App Initialization ---
app = Flask(__name__) # Gunicorn expects an 'app' object

# --- Configuration Loading ---
config = configparser.ConfigParser(interpolation=None)
config_path = '/app/config.ini' # Standard path in Docker container
if not os.path.exists(config_path):
    # Fallback for local development if /app/config.ini isn't there
    alt_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
    if os.path.exists(alt_config_path):
        config_path = alt_config_path
    else:
        logging.basicConfig(level=logging.ERROR)
        logger_fallback = logging.getLogger(__name__)
        logger_fallback.error(f"CRITICAL: config.ini not found at /app/config.ini or {alt_config_path}. Please ensure it exists.")
        sys.exit(1) # Critical error, cannot proceed without config

config.read(config_path)

IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images') # Docker path
LOG_DIR = config.get('General', 'LogDirectory', fallback='/app/logs') # Docker path
LOG_FILE = os.path.join(LOG_DIR, 'anpr_db_manager.log') # Specific log file for this service


# Ensure IMAGE_DIR and LOG_DIR exist
os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


# --- Database Configuration ---
DB_HOST = os.getenv('DB_HOST', 'mariadb')
DB_USER = os.getenv('MYSQL_USER', 'anpr_user')
DB_PASSWORD = os.getenv('MYSQL_PASSWORD')
DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')

# File Handler
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(log_formatter)

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

# Configure Flask's logger and the script's logger
logger = logging.getLogger(__name__) # For general script logging
app.logger.handlers = [] # Clear default Flask handlers
app.logger.propagate = False # Prevent Flask logs from going to root logger if not desired

for handler in [file_handler, console_handler]:
    logger.addHandler(handler)
    app.logger.addHandler(handler) # Add our handlers to Flask's logger

logger.setLevel(logging.INFO)
app.logger.setLevel(logging.INFO)

if not DB_PASSWORD:
    logger.critical("CRITICAL: MYSQL_PASSWORD environment variable not set. Database operations will fail.")
    # Don't exit here, let health check fail

DB_CONNECTION = None
TABLE_INITIALIZED = False # Flag to track if table creation was successful

def sanitize_filename(name_part):
    if not name_part or name_part == "N/A": return "NO_PLATE"
    return re.sub(r'[\\/*?:"<>|]', "_", name_part)

def initialize_database(is_startup=False):
    global DB_CONNECTION, TABLE_INITIALIZED
    attempts = 0
    # On startup, be more persistent. For runtime reconnections, fewer attempts might be desired.
    max_attempts = 10 if is_startup else 3
    retry_delay = 5

    while attempts < max_attempts:
        try:
            logger.info(f"Attempting to connect to database (Attempt {attempts + 1}/{max_attempts})...")
            conn = mysql.connector.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                connection_timeout=10 # Added connection timeout
            )
            if conn.is_connected():
                logger.info(f"Successfully connected to MariaDB database: {DB_NAME} on {DB_HOST}")
                DB_CONNECTION = conn
                try:
                    logger.info("Ensuring anpr_events table exists...")
                    cursor = DB_CONNECTION.cursor()
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS anpr_events (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            Timestamp DATETIME,
                            PlateNumber VARCHAR(255),
                            EventType VARCHAR(255),
                            CameraID VARCHAR(255),
                            VehicleType VARCHAR(255),
                            VehicleColor VARCHAR(255),
                            PlateColor VARCHAR(255),
                            ImageFilename VARCHAR(255),
                            DrivingDirection VARCHAR(255),
                            VehicleSpeed INT,             -- Added from anpr_web.py
                            Lane VARCHAR(50),             -- Added from anpr_web.py
                            ReceivedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            INDEX idx_plate_number (PlateNumber),
                            INDEX idx_camera_id (CameraID),
                            INDEX idx_timestamp (Timestamp),
                            INDEX idx_vehicle_type (VehicleType),
                            INDEX idx_vehicle_color (VehicleColor),
                            INDEX idx_driving_direction (DrivingDirection)
                        )
                    """)
                    DB_CONNECTION.commit()
                    # Verify table creation for health check
                    cursor.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{DB_NAME}' AND table_name = 'anpr_events'")
                    if cursor.fetchone()[0] == 1:
                        TABLE_INITIALIZED = True
                        logger.info("Table anpr_events ensured and verified.")
                    else:
                        TABLE_INITIALIZED = False
                        logger.error("Failed to verify anpr_events table creation.")
                    cursor.close()
                except mysql.connector.Error as err_table:
                    TABLE_INITIALIZED = False
                    logger.error(f"Failed to create/ensure anpr_events table: {err_table}")
                return True # Connection successful
        except mysql.connector.Error as e:
            logger.warning(f"Failed to connect to database: {e}. Retrying in {retry_delay} seconds...")
            attempts += 1
            time.sleep(retry_delay)
    
    logger.critical(f"Could not connect to the database after {max_attempts} attempts.")
    if is_startup and not DB_CONNECTION: # Only exit if it's a startup failure and no connection was ever made
        # For Gunicorn, exiting might lead to worker restarts. Health check will handle unhealthiness.
        # sys.exit(1) # Removed sys.exit to allow Gunicorn to manage the process
        pass
    return False # Connection failed

def get_db_connection():
    global DB_CONNECTION
    try:
        if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
            logger.warning("DB_CONNECTION is not available or not connected. Attempting to reconnect...")
            initialize_database(is_startup=False) # Attempt to reconnect with fewer retries

        if DB_CONNECTION and DB_CONNECTION.is_connected():
            # Optional: Ping the server to ensure connection is live before returning
            # DB_CONNECTION.ping(reconnect=True, attempts=1, delay=1)
            return DB_CONNECTION
        else:
            logger.error("Failed to re-establish database connection.")
            return None
    except mysql.connector.Error as e:
        logger.error(f"Database connection check/ping failed: {e}. Attempting to re-initialize.")
        initialize_database(is_startup=False)
        return DB_CONNECTION # Return whatever state it's in (might be None)


@app.route('/event', methods=['POST'])
def receive_event():
    """
    Receives ANPR event data (assumed to be JSON) and an image (optional).
    Saves the image and stores event details in the database.
    This endpoint is intended to be called by anpr_listener.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"status": "error", "message": "Database connection unavailable"}), 503

    try:
        event_data = request.json
        if not event_data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        logger.info(f"Received event data: {event_data}")

        # Image saving logic - assuming image might be sent separately or path provided
        # For this manager, we'll assume image is already saved and filename is in event_data
        # or that anpr_listener is responsible for placing it where anpr_web can find it.
        # This DB manager's primary role is to log the event metadata.
        # If anpr-listener sends image data to this endpoint, it needs to be handled (e.g. request.files)

        image_filename = event_data.get("ImageFilename") # Expecting filename from listener

        # If ImageFilename is not provided, but image data could be, handle here.
        # For now, we rely on ImageFilename.

        insert_anpr_event_db(event_data, image_filename, conn) # Pass conn

        return jsonify({"status": "success", "message": "Event processed"}), 201

    except Exception as e:
        logger.error(f"Error processing event: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def insert_anpr_event_db(event_data, image_filename_from_event, db_conn): # Added db_conn parameter
    # This function is now called by the /event endpoint, db_conn should be valid.
    if not db_conn or not db_conn.is_connected():
        logger.error("Cannot insert event, database connection is not valid.")
        # Optionally, try to reconnect once more, but the endpoint should handle this.
        return False


    # Ensure all expected fields are present, providing defaults or handling missing data
    sql = '''INSERT INTO anpr_events
             (Timestamp, PlateNumber, EventType, CameraID, VehicleType, VehicleColor, PlateColor,
              ImageFilename, DrivingDirection, VehicleSpeed, Lane)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'''
    try:
        plate_num_cleaned = event_data.get("PlateNumber", "N/A").strip()
        data_tuple = (
            event_data.get("Timestamp"),
            plate_num_cleaned,
            event_data.get("EventType"),
            event_data.get("CameraID"),
            event_data.get("VehicleType"),
            event_data.get("VehicleColor"),
            event_data.get("PlateColor"),
            image_filename_from_event, # Use the filename passed to the function
            event_data.get("DrivingDirection"),
            event_data.get("VehicleSpeed"), # Added
            event_data.get("Lane")          # Added
        )

        cursor = db_conn.cursor()
        cursor.execute(sql, data_tuple)
        db_conn.commit()
        logger.info(f"Event for plate '{plate_num_cleaned}' inserted successfully. DB Row ID: {cursor.lastrowid}")
        cursor.close()
        return True

    except mysql.connector.Error as e:
        logger.error(f"Error inserting into DB: {e}. Data: {event_data}", exc_info=True)
        # Consider if a rollback is needed if part of a larger transaction
        return False


# save_image function seems more appropriate for anpr_listener or a shared utility if needed here.
# For anpr_db_manager, it primarily stores event data.
# If anpr_listener calls this service with image data, then save_image would be relevant here.
# Based on current setup, anpr_listener saves image then calls anpr_web, which calls this.
# Let's assume anpr_listener might also directly call db_manager with event data that includes ImageFilename.

# def save_image(image_buffer, camera_id, timestamp_utc, plate_number):
#     # This function is identical to the one in anpr_listener.
#     # If this service is also responsible for saving images, it needs to be called.
#     # For now, assuming image_filename is provided in the event_data.
#     pass


@app.route('/health', methods=['GET'])
def health_check():
    global TABLE_INITIALIZED
    db_connected = False
    table_ok = TABLE_INITIALIZED # Use the flag set during/after initialize_database

    conn = get_db_connection() # This will attempt to connect/reconnect if necessary

    if conn and conn.is_connected():
        db_connected = True
        # Double check table status if connection was just (re)established and TABLE_INITIALIZED is false
        if not table_ok:
            try:
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{DB_NAME}' AND table_name = 'anpr_events'")
                if cursor.fetchone()[0] == 1:
                    table_ok = True
                    TABLE_INITIALIZED = True # Update global flag
                    logger.info("Health check: Table 'anpr_events' confirmed to exist.")
                else:
                    logger.warning("Health check: Table 'anpr_events' still not found.")
                cursor.close()
            except mysql.connector.Error as e:
                logger.error(f"Health check: Database error while re-checking for table: {e}")
                # table_ok remains false
            except Exception as e_gen:
                 logger.error(f"Health check: Generic error while re-checking for table: {e_gen}")
    else: # conn is None or not connected
        db_connected = False
        table_ok = False # If no DB connection, table cannot be confirmed.

    if db_connected and table_ok:
        return jsonify({"status": "healthy", "database_connection": "ok", "anpr_events_table": "exists"}), 200
    elif db_connected and not table_ok:
        # This state means DB is connected, but table creation failed or is pending.
        # Critical for service readiness.
        return jsonify({"status": "unhealthy", "database_connection": "ok", "anpr_events_table": "missing_or_failed"}), 503
    else: # db_connected is False
        return jsonify({"status": "unhealthy", "database_connection": "error", "anpr_events_table": "unknown"}), 503


def close_db_connection_on_exit(): # Renamed to be more specific
    global DB_CONNECTION
    if DB_CONNECTION and DB_CONNECTION.is_connected():
        DB_CONNECTION.close()
        logger.info("MariaDB connection closed on application exit.")
        DB_CONNECTION = None

# Initialize database at application startup (when Gunicorn loads the app)
# This is crucial for the health check to pass eventually.
initialize_database(is_startup=True)

import atexit
atexit.register(close_db_connection_on_exit)

if __name__ == '__main__':
    # This block is for direct execution (python anpr_db_manager.py)
    # Gunicorn will use the 'app' object directly.
    # Ensure DB is initialized if running directly.
    # initialize_database() was already called globally for Gunicorn.

    server_port = int(os.getenv('FLASK_RUN_PORT', 5001))
    logger.info(f"Starting ANPR DB Manager Flask server directly on http://0.0.0.0:{server_port}...")
    app.run(host='0.0.0.0', port=server_port, debug=False) # debug=False for production/gunicorn consistency
