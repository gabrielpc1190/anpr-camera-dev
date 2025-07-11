import os
import sys
import logging
import configparser
import datetime
import math
import mysql.connector
from flask import Flask, jsonify, request, render_template, send_from_directory

# --- Application Setup ---
app = Flask(__name__, template_folder='templates')

# --- Configuration Loading ---
config = configparser.ConfigParser(interpolation=None)
CONFIG_FILE_PATH = '/app/config.ini'  # Primary path for Docker
if not os.path.exists(CONFIG_FILE_PATH):
    # Try relative path for local development if /app/config.ini not found
    CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')

if not os.path.exists(CONFIG_FILE_PATH):
    logging.basicConfig(level=logging.ERROR) # Basic logging if config fails
    logger_fallback = logging.getLogger(__name__)
    logger_fallback.error(f"CRITICAL: config.ini not found at /app/config.ini or local path {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')}. Please ensure it exists.")
    sys.exit(1)
config.read(CONFIG_FILE_PATH)

# --- Global Variables & Paths from Config ---
IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images')
# For anpr_web.py, use a distinct log file name.
LOG_FILE_PATH = os.path.join(config.get('General', 'LogDirectory', fallback='/app/logs'), 'anpr_web.log')


# Ensure log directory exists
# The directory for LOG_FILE_PATH must exist before logger is configured.
try:
    os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
except FileNotFoundError: # Handle cases where path is relative and intermediate dirs don't exist
    # This might happen if LogDirectory itself is just a filename, not a path.
    # For robustness, let's assume LogDirectory is indeed a directory.
    pass


# --- Logging Setup ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')

# File Handler
try:
    file_handler = logging.FileHandler(LOG_FILE_PATH)
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
except Exception as e:
    logging.basicConfig(level=logging.WARNING) # Fallback to basicConfig if file handler fails
    logger_fallback = logging.getLogger(__name__)
    logger_fallback.warning(f"Could not create file handler for {LOG_FILE_PATH} due to {e}. Logging to console only for this module.")


# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)


# --- Database Configuration ---
DB_HOST = os.getenv('DB_HOST', 'mariadb')
DB_USER = os.getenv('MYSQL_USER', 'anpr_user')
DB_PASSWORD = os.getenv('MYSQL_PASSWORD')
DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')

# Check for MYSQL_PASSWORD and log critically if not set, but do not exit immediately.
# The application will attempt to start, and health checks or DB operations will fail if it's truly missing.
if not DB_PASSWORD:
    logger.critical("CRITICAL: MYSQL_PASSWORD environment variable not set. Database operations will fail.")

DB_CONNECTION = None

def initialize_database():
    global DB_CONNECTION
    attempts = 0
    max_attempts = 10
    retry_delay = 5

    while attempts < max_attempts:
        try:
            logger.info(f"Attempting to connect to database (Attempt {attempts + 1}/{max_attempts})...")
            conn = mysql.connector.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                connection_timeout=10
            )
            if conn.is_connected():
                logger.info(f"Successfully connected to MariaDB database: {DB_NAME} on {DB_HOST}")
                DB_CONNECTION = conn
                return True
        except mysql.connector.Error as e:
            logger.warning(f"Failed to connect to database: {e}. Retrying in {retry_delay} seconds...")
            attempts += 1
            import time # local import for sleep
            time.sleep(retry_delay)

    logger.critical("Could not connect to the database after multiple attempts.")
    return False

def get_db_connection():
    global DB_CONNECTION
    try:
        if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
            logger.info("Database connection lost or not initialized. Attempting to reconnect...")
            if not initialize_database(): # Try to initialize
                logger.error("Failed to re-establish database connection.")
                return None # Return None if connection failed
        else:
            # Ping the connection to ensure it's alive
            DB_CONNECTION.ping(reconnect=True, attempts=3, delay=1)
            logger.debug("Database connection is active.")
        return DB_CONNECTION
    except mysql.connector.Error as e:
        logger.error(f"Database connection check failed: {e}. Attempting to re-initialize.")
        if not initialize_database(): # Try to re-establish
             logger.error("Failed to re-establish database connection after ping failure.")
             return None
        return DB_CONNECTION # Return whatever state it's in (might be None)

# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    logger.debug(f"Attempting to serve image: {filename} from directory: {IMAGE_DIR}")
    # Ensure IMAGE_DIR is absolute or correctly relative to the app's execution context
    # For send_from_directory, it's often safer if it's an absolute path.
    # However, config.get should provide a path that's usable.
    return send_from_directory(os.path.abspath(IMAGE_DIR), filename)

@app.route('/api/events', methods=['GET'])
def get_events():
    conn = get_db_connection()
    if not conn: # Check if conn is None
        logger.error("No database connection for /api/events")
        return jsonify({"error": "Database connection failed"}), 503 # Service Unavailable

    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        offset = (page - 1) * limit

        # Filters
        filter_clauses = []
        params = []

        # String fields
        # Mapping from query param to DB column name
        field_map = {
            'plate': 'PlateNumber',
            'camera_id': 'CameraID',
            'vehicle_type': 'VehicleType',
            'vehicle_color': 'VehicleColor',
            'driving_direction': 'DrivingDirection'
        }

        for query_param, db_column in field_map.items():
            value = request.args.get(query_param)
            if value:
                if query_param == 'plate': # Use LIKE for plate number
                    filter_clauses.append(f"{db_column} LIKE %s")
                    params.append(f"%{value}%")
                else: # Exact match for others
                    filter_clauses.append(f"{db_column} = %s")
                    params.append(value)

        # Date fields
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if start_date_str:
            filter_clauses.append("Timestamp >= %s")
            params.append(start_date_str) # Assumes JS sends it in 'YYYY-MM-DD HH:MM:SS' format
        if end_date_str:
            filter_clauses.append("Timestamp <= %s")
            params.append(end_date_str)

        where_clause = " AND ".join(filter_clauses) if filter_clauses else "1=1"

        # Count total events for pagination
        count_query = f"SELECT COUNT(*) FROM anpr_events WHERE {where_clause}"
        cursor = conn.cursor()
        logger.debug(f"Executing count query: {count_query} with params: {tuple(params)}")
        cursor.execute(count_query, tuple(params))
        total_events = cursor.fetchone()[0]
        total_pages = math.ceil(total_events / limit) if limit > 0 else 0

        # Fetch events for the current page
        # Added all relevant columns from anpr_db_manager's table definition
        query = f"""
            SELECT Timestamp, PlateNumber, EventType, CameraID, VehicleType,
                   VehicleColor, PlateColor, ImageFilename, DrivingDirection,
                   VehicleSpeed, Lane, ReceivedAt
            FROM anpr_events
            WHERE {where_clause}
            ORDER BY Timestamp DESC
            LIMIT %s OFFSET %s
        """
        final_params_for_query = tuple(params + [limit, offset])
        logger.debug(f"Executing select query: {query} with params: {final_params_for_query}")
        cursor.execute(query, final_params_for_query)

        events = []
        columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            event = dict(zip(columns, row))
            # Format Timestamp and ReceivedAt as ISO string or desired format
            for ts_field in ['Timestamp', 'ReceivedAt']:
                if event.get(ts_field) and isinstance(event[ts_field], datetime.datetime):
                    event[ts_field] = event[ts_field].isoformat(sep=' ', timespec='milliseconds')
            events.append(event)

        cursor.close()

        return jsonify({
            "events": events,
            "current_page": page,
            "total_pages": total_pages,
            "total_events": total_events
        })

    except mysql.connector.Error as e:
        logger.error(f"Database error in /api/events: {e}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in /api/events: {e}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred"}), 500


@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 503

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT CameraID FROM anpr_events WHERE CameraID IS NOT NULL AND CameraID != '' ORDER BY CameraID")
        cameras = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return jsonify({"cameras": cameras})
    except mysql.connector.Error as e:
        logger.error(f"Database error in /api/cameras: {e}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in /api/cameras: {e}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/api/events/latest_timestamp', methods=['GET'])
def get_latest_event_timestamp():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 503

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(Timestamp) FROM anpr_events")
        latest_ts_db = cursor.fetchone()[0]
        cursor.close()

        latest_timestamp_iso = None
        if latest_ts_db and isinstance(latest_ts_db, datetime.datetime):
            latest_timestamp_iso = latest_ts_db.isoformat(sep=' ', timespec='milliseconds')

        return jsonify({"latest_timestamp": latest_timestamp_iso})
    except mysql.connector.Error as e:
        logger.error(f"Database error in /api/events/latest_timestamp: {e}", exc_info=True)
        return jsonify({"error": "Database query failed"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in /api/events/latest_timestamp: {e}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    db_connected = False
    table_exists = False
    conn = get_db_connection() # This will attempt to initialize if needed

    if conn and conn.is_connected():
        db_connected = True
        try:
            cursor = conn.cursor()
            # Check if the anpr_events table exists
            cursor.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{DB_NAME}' AND table_name = 'anpr_events'")
            if cursor.fetchone()[0] == 1:
                table_exists = True
            else:
                logger.warning(f"Health check for anpr_web: Table 'anpr_events' does not exist in database '{DB_NAME}'.")
            cursor.close()
        except mysql.connector.Error as e:
            logger.error(f"Health check for anpr_web: Database error while checking for table: {e}")
            # If query fails, consider DB not fully healthy for this check, but connection itself might be ok
            # We will rely on table_exists being false.
        except Exception as e_gen:
            logger.error(f"Health check for anpr_web: Generic error while checking for table: {e_gen}")
            # Treat generic errors as a problem too

    if db_connected and table_exists:
        return jsonify({"status": "healthy", "database_connection": "ok", "anpr_events_table": "exists"}), 200
    elif db_connected and not table_exists:
        # This state might occur if anpr_db_manager hasn't created the table yet.
        # The service is up, DB is connected, but not fully ready for queries.
        return jsonify({"status": "unhealthy", "database_connection": "ok", "anpr_events_table": "missing"}), 503
    else: # db_connected is False
        return jsonify({"status": "unhealthy", "database_connection": "error", "anpr_events_table": "unknown"}), 503

def close_db_connection_on_exit():
    global DB_CONNECTION
    if DB_CONNECTION and DB_CONNECTION.is_connected():
        DB_CONNECTION.close()
        logger.info("MariaDB connection closed on application exit.")

if __name__ == '__main__':
    # Ensure DB is initialized at startup (or attempt to)
    if not initialize_database():
        logger.warning("Database initialization failed at startup. API might not function correctly for DB operations until DB is available.")

    import atexit
    atexit.register(close_db_connection_on_exit)

    server_port = int(os.getenv('FLASK_RUN_PORT', 5000)) # Default port 5000 for web UI
    logger.info(f"Starting ANPR Web UI Flask server on http://0.0.0.0:{server_port}...")
    # When running with Gunicorn, Gunicorn handles worker management and binding.
    # This app.run() is primarily for direct execution (local development).
    app.run(host='0.0.0.0', port=server_port, debug=False)
