import time, sys, logging, os, re, configparser, uuid, json, datetime
import mysql.connector
from flask import Flask, request, jsonify

# --- Application Setup ---
app = Flask(__name__)

# --- Configuration Loading ---
config = configparser.ConfigParser(interpolation=None)
CONFIG_FILE_PATH = '/app/config.ini'  # Primary path for Docker
if not os.path.exists(CONFIG_FILE_PATH):
    CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini') # Fallback for local

if not os.path.exists(CONFIG_FILE_PATH):
    # Use basic logging if config file for logger is not found yet
    logging.basicConfig(level=logging.ERROR)
    logging.error(f"CRITICAL: config.ini not found at /app/config.ini or local path. Please ensure it exists.")
    sys.exit(1)
config.read(CONFIG_FILE_PATH)

# --- Global Variables & Paths from Config ---
IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images') # Default for Docker
LOG_FILE_PATH = config.get('General', 'LogFile', fallback='/app/logs/anpr_db_manager.log') # Default for Docker

# Ensure IMAGE_DIR and log directory exist
os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)


# --- Logging Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')

# File Handler
file_handler = logging.FileHandler(LOG_FILE_PATH)
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)


# --- Database Configuration ---
DB_HOST = os.getenv('DB_HOST', 'mariadb')
DB_USER = os.getenv('MYSQL_USER', 'anpr_user')
DB_PASSWORD = os.getenv('MYSQL_PASSWORD') # Should be set in .env
DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')

if not DB_PASSWORD:
    logger.critical("MYSQL_PASSWORD environment variable not set. Cannot connect to database.")
    sys.exit(1)

DB_CONNECTION = None # Global database connection object

def sanitize_filename(name_part):
    if not name_part or name_part == "N/A": return "NO_PLATE"
    return re.sub(r'[\\/*?:"<>| ]', "_", name_part) # Added space removal

def initialize_database():
    global DB_CONNECTION
    attempts = 0
    max_attempts = 10 # Increased for robustness during startup
    retry_delay = 5 # seconds

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
                ensure_anpr_events_table() # Call table creation/check
                return True
        except mysql.connector.Error as e:
            logger.warning(f"Failed to connect to database: {e}. Retrying in {retry_delay} seconds...")
            attempts += 1
            time.sleep(retry_delay)
    
    logger.critical("Could not connect to the database after multiple attempts. Exiting.")
    # sys.exit(1) # Consider if exiting is always desired or if app should run w/o DB temporarily
    return False

def ensure_anpr_events_table():
    if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
        logger.error("Cannot ensure table, database not connected.")
        return
    try:
        logger.info("Ensuring anpr_events table exists...")
        cursor = DB_CONNECTION.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anpr_events (
                id INT AUTO_INCREMENT PRIMARY KEY,
                Timestamp DATETIME(3),
                PlateNumber VARCHAR(50),
                EventType VARCHAR(50),
                CameraID VARCHAR(100),
                VehicleType VARCHAR(50),
                VehicleColor VARCHAR(50),
                PlateColor VARCHAR(50),
                ImageFilename VARCHAR(255),
                DrivingDirection VARCHAR(50),
                VehicleSpeed INT,
                Lane INT,
                ReceivedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_plate_number (PlateNumber),
                INDEX idx_camera_id (CameraID),
                INDEX idx_timestamp (Timestamp),
                INDEX idx_vehicle_type (VehicleType),
                INDEX idx_vehicle_color (VehicleColor),
                INDEX idx_driving_direction (DrivingDirection)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """) # Added DATETIME(3) for milliseconds, increased some VARCHAR sizes, specified engine and charset
        DB_CONNECTION.commit()
        cursor.close()
        logger.info("Table anpr_events ensured/created successfully.")
    except mysql.connector.Error as err_table:
        logger.error(f"Failed to create/ensure anpr_events table: {err_table}", exc_info=True)

def get_db_connection():
    """Gets a new DB connection or pings existing one. Reconnects if necessary."""
    global DB_CONNECTION
    try:
        if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
            logger.info("Database connection lost or not initialized. Attempting to reconnect...")
            initialize_database()
        else:
            # Ping the connection to ensure it's alive
            DB_CONNECTION.ping(reconnect=True, attempts=3, delay=1)
            logger.debug("Database connection is active.")
        return DB_CONNECTION
    except mysql.connector.Error as e:
        logger.error(f"Database connection check failed: {e}. Attempting to re-initialize.")
        initialize_database() # Try to re-establish
        return DB_CONNECTION # Return whatever state it's in (might be None)


def insert_anpr_event_db(event_data):
    conn = get_db_connection()
    if conn is None or not conn.is_connected():
        logger.error("Failed to get active database connection. Cannot insert event.")
        return False

    # Ensure all expected fields are present, providing defaults for missing optional fields
    # This matches the anpr_listener event_details structure
    sql_columns = [
        "Timestamp", "PlateNumber", "EventType", "CameraID",
        "VehicleType", "VehicleColor", "PlateColor", "ImageFilename",
        "DrivingDirection", "VehicleSpeed", "Lane"
    ]

    # Prepare data tuple, using .get() with None as default for potentially missing fields
    data_tuple = (
        event_data.get("timestamp_capture"), # From listener: "timestamp_capture"
        event_data.get("plate_number", "").strip(),
        event_data.get("event_type"),
        event_data.get("camera_id"),
        event_data.get("vehicle_type"),
        event_data.get("vehicle_color"),
        event_data.get("plate_color"),
        event_data.get("image_filename"), # This will be set by save_image_from_request
        event_data.get("driving_direction"),
        event_data.get("vehicle_speed"),
        event_data.get("lane")
    )

    sql = f'''INSERT INTO anpr_events ({', '.join(sql_columns)})
             VALUES ({', '.join(['%s'] * len(sql_columns))})'''

    try:
        cursor = conn.cursor()
        cursor.execute(sql, data_tuple)
        conn.commit()
        row_id = cursor.lastrowid
        logger.info(f"Event for plate '{event_data.get('plate_number')}' inserted successfully. DB Row ID: {row_id}")
        cursor.close()
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error inserting event into DB: {e}. Data: {event_data}", exc_info=True)
        conn.rollback() # Rollback on error
        return False
    except Exception as e_gen:
        logger.error(f"Generic error during event insertion: {e_gen}. Data: {event_data}", exc_info=True)
        if conn and conn.is_connected(): conn.rollback()
        return False
def save_image_from_request(image_file, event_data):
    """Saves the image from the request and returns the filename, or None."""
    if not image_file:
        logger.info("No image file provided with the event.")
        return None

    try:
        # Parse timestamp from event_data to use in filename
        # Assuming timestamp_capture is in ISO format: "YYYY-MM-DDTHH:MM:SS.ffffff"
        ts_iso = event_data.get("timestamp_capture")
        if ts_iso:
            dt_object = datetime.datetime.fromisoformat(ts_iso)
            # Format for filename, e.g., YYYYMMDD_HHMMSS_ms
            ts_for_fn = dt_object.strftime("%Y%m%d_%H%M%S_%f")[:-3] # Keep milliseconds
        else:
            # Fallback if timestamp is missing, though it shouldn't be
            ts_for_fn = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            logger.warning("Timestamp missing in event_data for image naming, using current time.")

        camera_id = sanitize_filename(event_data.get("camera_id", "UNKNOWN_CAM"))
        plate_number = sanitize_filename(event_data.get("plate_number", "NO_PLATE"))
        image_uuid = uuid.uuid4().hex[:8] # Shorter UUID
        
        image_basename = f"{ts_for_fn}_{camera_id}_{plate_number}_{image_uuid}.jpg"
        img_fn_with_path = os.path.join(IMAGE_DIR, image_basename)

        image_file.save(img_fn_with_path) # Flask FileStorage object has a save method
        logger.info(f"Image saved: {img_fn_with_path}")
        return image_basename
    except Exception as e:
        logger.error(f"Error saving image: {e}", exc_info=True)
        return None

@app.route('/event', methods=['POST'])
def handle_event():
    logger.info(f"Received event request. Headers: {request.headers}, Form keys: {list(request.form.keys())}, Files: {list(request.files.keys())}")

    if 'event_data' not in request.form:
        logger.error("Missing 'event_data' in form submission.")
        return jsonify({"status": "error", "message": "Missing event_data"}), 400

    try:
        event_data_json = request.form['event_data']
        event_data = json.loads(event_data_json)
        logger.debug(f"Received event_data (JSON): {event_data}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode event_data JSON: {e}. Data: {request.form.get('event_data')}")
        return jsonify({"status": "error", "message": "Invalid JSON in event_data"}), 400
    except Exception as e_gen:
        logger.error(f"Error processing form data: {e_gen}")
        return jsonify({"status": "error", "message": "Error processing form data"}), 500

    image_file = request.files.get('image') # Get image if present

    # Save image first, get filename
    image_filename = save_image_from_request(image_file, event_data)
    event_data['image_filename'] = image_filename # Add/update image_filename in event_data

    # Insert event into database
    if insert_anpr_event_db(event_data):
        logger.info(f"Successfully processed event for plate: {event_data.get('plate_number', 'N/A')}")
        return jsonify({"status": "success", "message": "Event processed"}), 201
    else:
        logger.error(f"Failed to insert event into DB for plate: {event_data.get('plate_number', 'N/A')}")
        # Potentially delete saved image if DB insert fails? For now, keeping it simple.
        return jsonify({"status": "error", "message": "Failed to save event to database"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    db_connected = False
    table_exists = False
    conn = get_db_connection()

    if conn and conn.is_connected():
        db_connected = True
        try:
            cursor = conn.cursor()
            # Check if the anpr_events table exists
            # Using database name from env/default to qualify table name if needed, though usually not if DB is selected.
            cursor.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{DB_NAME}' AND table_name = 'anpr_events'")
            if cursor.fetchone()[0] == 1:
                table_exists = True
            else:
                logger.warning(f"Health check: Table 'anpr_events' does not exist in database '{DB_NAME}'.")
            cursor.close()
        except mysql.connector.Error as e:
            logger.error(f"Health check: Database error while checking for table: {e}")
            db_connected = False # If query fails, consider DB not fully healthy for this check
        except Exception as e_gen:
            logger.error(f"Health check: Generic error while checking for table: {e_gen}")
            db_connected = False # Treat generic errors as a problem too

    if db_connected and table_exists:
        return jsonify({"status": "healthy", "database_connection": "ok", "anpr_events_table": "exists"}), 200
    elif db_connected and not table_exists:
        return jsonify({"status": "unhealthy", "database_connection": "ok", "anpr_events_table": "missing"}), 503
    else: # db_connected is False
        return jsonify({"status": "unhealthy", "database_connection": "error", "anpr_events_table": "unknown"}), 503

def close_db_connection_on_exit():
    global DB_CONNECTION
    if DB_CONNECTION and DB_CONNECTION.is_connected():
        DB_CONNECTION.close()
        logger.info("MariaDB connection closed on application exit.")

if __name__ == '__main__':
    if not initialize_database():
        logger.warning("Database initialization failed. API might not function correctly for DB operations.")
        # Decide if the app should exit or run with limited functionality
        # For now, it will run, but DB operations will likely fail until DB is up.

    import atexit
    atexit.register(close_db_connection_on_exit)

    # Port should be configurable, e.g., via environment variable
    server_port = int(os.getenv('FLASK_RUN_PORT', 5001))
    logger.info(f"Starting ANPR DB Manager Flask server on port {server_port}...")
    # For development, use Flask's built-in server.
    # For production, Gunicorn will be specified in docker-compose.yml or another process manager.
    app.run(host='0.0.0.0', port=server_port, debug=False) # debug=False for production-like behavior
