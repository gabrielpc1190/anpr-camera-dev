import os
import sys
import logging
import configparser

# --- Aggressive Early Logging Setup ---
# This logger writes to a file in the bind-mounted /app/logs directory,
# so it will be available on the host at ./app/logs/anpr_web_startup.log
EARLY_LOG_FILE_PATH = '/app/logs/anpr_web_startup.log'
early_logger = logging.getLogger('anpr_web_early_startup')
early_logger.setLevel(logging.DEBUG) # Capture everything for this logger
try:
    # Ensure the directory /app/logs exists from the container's perspective
    # The bind mount from docker-compose should handle host directory creation via setup.sh
    # os.makedirs(os.path.dirname(EARLY_LOG_FILE_PATH), exist_ok=True) # This might be problematic if /app/logs isn't there yet from Docker's POV

    early_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')
    early_fh = logging.FileHandler(EARLY_LOG_FILE_PATH, mode='a') # Append mode
    early_fh.setFormatter(early_formatter)
    early_logger.addHandler(early_fh)
    early_logger.info('--- anpr_web.py script execution started (early log) ---')
except Exception as e:
    # If this fails, something is very wrong. Try to print to stderr.
    # This message might only be visible if the container's command allows stdout/stderr to be captured.
    import sys
    sys.stderr.write(f"CRITICAL_ERROR_EARLY_LOG: Failed to initialize early_logger to {EARLY_LOG_FILE_PATH}: {e}\n")
    # We can't use early_logger here as it failed to initialize.
# --- End of Aggressive Early Logging Setup ---

early_logger.info("Attempting to import remaining modules...")
try:
    import datetime
    import math
    import mysql.connector
    from flask import Flask, jsonify, request, render_template, send_from_directory
    early_logger.info("Successfully imported remaining modules.")
except Exception as e:
    early_logger.exception("CRITICAL_ERROR_IMPORT: Failed to import one or more modules.")
    # Exiting here because the app cannot run without these imports.
    # The early_startup.log should contain the traceback.
    sys.exit(1) # Ensure Gunicorn/Docker knows this worker is bad.

# --- Application Setup ---
early_logger.info("Attempting to create Flask app object...")
try:
    app = Flask(__name__, template_folder='templates')
    early_logger.info("Flask app object created successfully.")
except Exception as e:
    early_logger.exception("CRITICAL_ERROR_FLASK_APP: Failed to create Flask app object.")
    sys.exit(1)

# --- Configuration Loading ---
early_logger.info("Attempting to load configuration (config.ini)...")
config = configparser.ConfigParser(interpolation=None)
CONFIG_FILE_PATH = '/app/config.ini'  # Primary path for Docker
try:
    if not os.path.exists(CONFIG_FILE_PATH):
        early_logger.warning(f"Config file {CONFIG_FILE_PATH} not found. Trying alternative path for local dev...")
        # Try relative path for local development if /app/config.ini not found
        alt_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
        if os.path.exists(alt_config_path):
            CONFIG_FILE_PATH = alt_config_path
            early_logger.info(f"Using alternative config path: {CONFIG_FILE_PATH}")
        else:
            early_logger.error(f"CRITICAL: config.ini not found at /app/config.ini or local path {alt_config_path}. Please ensure it exists.")
            # logging.basicConfig(level=logging.ERROR) # Basic logging if config fails - early_logger is already active
            # logger_fallback = logging.getLogger(__name__) # Use early_logger
            # logger_fallback.error(f"CRITICAL: config.ini not found at /app/config.ini or local path {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')}. Please ensure it exists.")
            sys.exit(1) # Cannot proceed without config

    config.read(CONFIG_FILE_PATH)
    early_logger.info(f"Successfully loaded configuration from {CONFIG_FILE_PATH}.")
except Exception as e:
    early_logger.exception(f"CRITICAL_ERROR_CONFIG_LOAD: Failed to load configuration from {CONFIG_FILE_PATH}.")
    sys.exit(1)


# --- Global Variables & Paths from Config ---
early_logger.info("Reading paths from configuration...")
try:
    IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images')
    # For anpr_web.py, use a distinct log file name for the main logger.
    MAIN_LOG_DIR = config.get('General', 'LogDirectory', fallback='/app/logs')
    MAIN_LOG_FILE_PATH = os.path.join(MAIN_LOG_DIR, 'anpr_web_main.log') # Changed name to avoid conflict
    early_logger.info(f"IMAGE_DIR set to: {IMAGE_DIR}")
    early_logger.info(f"MAIN_LOG_FILE_PATH set to: {MAIN_LOG_FILE_PATH}")
except Exception as e:
    early_logger.exception("CRITICAL_ERROR_CONFIG_READ_PATHS: Failed to read paths from config.")
    sys.exit(1)

# Ensure log directory for the main logger exists
# The directory for MAIN_LOG_FILE_PATH must exist before the main logger is configured.
early_logger.info(f"Ensuring main log directory exists: {os.path.dirname(MAIN_LOG_FILE_PATH)}")
try:
    os.makedirs(os.path.dirname(MAIN_LOG_FILE_PATH), exist_ok=True)
    early_logger.info("Main log directory ensured.")
except Exception as e: # Catch a broader exception here for directory creation
    early_logger.exception(f"CRITICAL_ERROR_MKDIR_MAIN_LOG: Failed to create directory for main logger at {os.path.dirname(MAIN_LOG_FILE_PATH)}.")
    # If we can't create the log dir, the main logger will fail.
    # Depending on policy, we might exit or try to continue with only early_logger.
    # For now, let it proceed, main logger setup will try/except.
    pass


# --- Main Logging Setup (distinct from early_logger) ---
early_logger.info("Attempting to set up the main application logger...")
logger = logging.getLogger(__name__) # This is the main app logger
logger.setLevel(logging.INFO)
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')

# File Handler for main logger
try:
    file_handler = logging.FileHandler(MAIN_LOG_FILE_PATH)
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
    early_logger.info(f"Main logger file handler configured for {MAIN_LOG_FILE_PATH}.")
except Exception as e:
    # logging.basicConfig(level=logging.WARNING) # Fallback to basicConfig if file handler fails - already have early_logger
    # logger_fallback = logging.getLogger(__name__) # Use early_logger
    early_logger.warning(f"Could not create file handler for main logger at {MAIN_LOG_FILE_PATH} due to {e}. Main logs might only go to console.")


# Console Handler for main logger
try:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)
    early_logger.info("Main logger console handler configured.")
except Exception as e:
    early_logger.warning(f"Could not create console handler for main logger due to {e}.")

early_logger.info("Main application logger setup complete (or attempted).")


# --- Database Configuration ---
early_logger.info("Configuring database connection parameters...")
try:
    DB_HOST = os.getenv('DB_HOST', 'mariadb')
    DB_USER = os.getenv('MYSQL_USER', 'anpr_user')
    DB_PASSWORD = os.getenv('MYSQL_PASSWORD')
    DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')
    early_logger.info(f"DB params: Host={DB_HOST}, User={DB_USER}, DBName={DB_NAME}, Password_Is_Set={'Yes' if DB_PASSWORD else 'No'}")
except Exception as e:
    early_logger.exception("CRITICAL_ERROR_DB_ENV_VARS: Failed to read database environment variables.")
    sys.exit(1)


# Check for MYSQL_PASSWORD and log critically if not set, but do not exit immediately.
# The application will attempt to start, and health checks or DB operations will fail if it's truly missing.
if not DB_PASSWORD:
    logger.critical("CRITICAL: MYSQL_PASSWORD environment variable not set. Database operations will fail.")
    early_logger.critical("CRITICAL: MYSQL_PASSWORD environment variable not set. This will likely cause DB connection failure.")

DB_CONNECTION = None
early_logger.info("DB_CONNECTION initialized to None.")


def initialize_database():
    global DB_CONNECTION
    early_logger.info("initialize_database() called.")
    logger.info("Attempting to initialize database connection...") # Use main logger for general ops
    attempts = 0
    max_attempts = 10 # Reduced for faster feedback if there's a persistent issue.
    retry_delay = 5

    while attempts < max_attempts:
        try:
            logger.info(f"Attempting to connect to database (Attempt {attempts + 1}/{max_attempts})...")
            early_logger.debug(f"DB connect attempt {attempts + 1} with host={DB_HOST}, user={DB_USER}, db={DB_NAME}")
            conn = mysql.connector.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                connection_timeout=10 # seconds
            )
            if conn.is_connected():
                logger.info(f"Successfully connected to MariaDB database: {DB_NAME} on {DB_HOST}")
                early_logger.info(f"DB connection successful to {DB_NAME} on {DB_HOST}.")
                DB_CONNECTION = conn
                return True
        except mysql.connector.Error as e:
            logger.warning(f"Failed to connect to database: {e}. Retrying in {retry_delay} seconds...")
            early_logger.warning(f"DB connect attempt {attempts + 1} failed: {e}")
            attempts += 1
            if attempts < max_attempts: # Avoid sleeping on the last attempt
                import time # local import for sleep
                time.sleep(retry_delay)
            else:
                early_logger.error(f"DB connection failed after {max_attempts} attempts. Last error: {e}")

    logger.critical("Could not connect to the database after multiple attempts.")
    early_logger.critical("initialize_database() failed to connect after multiple attempts.")
    return False

def get_db_connection():
    global DB_CONNECTION
    early_logger.debug("get_db_connection() called.")
    try:
        if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
            logger.info("Database connection lost or not initialized. Attempting to reconnect...")
            early_logger.info("DB_CONNECTION is None or not connected in get_db_connection(). Attempting initialize_database().")
            if not initialize_database(): # Try to initialize
                logger.error("Failed to re-establish database connection.")
                early_logger.error("get_db_connection(): initialize_database() failed.")
                return None # Return None if connection failed
        else:
            # Ping the connection to ensure it's alive
            early_logger.debug("Pinging existing DB connection.")
            DB_CONNECTION.ping(reconnect=True, attempts=3, delay=1)
            logger.debug("Database connection is active.")
            early_logger.debug("DB connection ping successful.")
        return DB_CONNECTION
    except mysql.connector.Error as e:
        logger.error(f"Database connection check failed: {e}. Attempting to re-initialize.")
        early_logger.exception(f"get_db_connection(): DB ping or check failed. Attempting initialize_database(). Error: {e}")
        if not initialize_database(): # Try to re-establish
             logger.error("Failed to re-establish database connection after ping failure.")
             early_logger.error("get_db_connection(): initialize_database() failed after ping failure.")
             return None
        return DB_CONNECTION # Return whatever state it's in (might be None)

# --- Routes ---
early_logger.info("Defining Flask routes...")

@app.route('/')
def index():
    early_logger.debug("Route / called.")
    return render_template('index.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    early_logger.debug(f"Route /images/{filename} called.")
    logger.debug(f"Attempting to serve image: {filename} from directory: {IMAGE_DIR}")
    # Ensure IMAGE_DIR is absolute or correctly relative to the app's execution context
    # For send_from_directory, it's often safer if it's an absolute path.
    # However, config.get should provide a path that's usable.
    return send_from_directory(os.path.abspath(IMAGE_DIR), filename)

@app.route('/api/events', methods=['GET'])
def get_events():
    early_logger.debug("Route /api/events called.")
    conn = get_db_connection()
    if not conn: # Check if conn is None
        logger.error("No database connection for /api/events")
        early_logger.error("/api/events: No DB connection.")
        return jsonify({"error": "Database connection failed"}), 503 # Service Unavailable

    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        offset = (page - 1) * limit
        early_logger.debug(f"/api/events: page={page}, limit={limit}")

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
        early_logger.debug(f"/api/events: where_clause='{where_clause}', params={params}")

        # Count total events for pagination
        count_query = f"SELECT COUNT(*) FROM anpr_events WHERE {where_clause}"
        cursor = conn.cursor()
        logger.debug(f"Executing count query: {count_query} with params: {tuple(params)}")
        early_logger.debug(f"Executing count query: {count_query} with params: {tuple(params)}")
        cursor.execute(count_query, tuple(params))
        total_events = cursor.fetchone()[0]
        total_pages = math.ceil(total_events / limit) if limit > 0 else 0
        early_logger.debug(f"/api/events: total_events={total_events}, total_pages={total_pages}")


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
        early_logger.debug(f"Executing select query: {query} with params: {final_params_for_query}")
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
        early_logger.debug(f"/api/events: Found {len(events)} events for current page.")

        return jsonify({
            "events": events,
            "current_page": page,
            "total_pages": total_pages,
            "total_events": total_events
        })

    except mysql.connector.Error as e:
        logger.error(f"Database error in /api/events: {e}", exc_info=True)
        early_logger.exception(f"/api/events: Database error: {e}")
        return jsonify({"error": "Database query failed"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in /api/events: {e}", exc_info=True)
        early_logger.exception(f"/api/events: Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500


@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    early_logger.debug("Route /api/cameras called.")
    conn = get_db_connection()
    if not conn:
        early_logger.error("/api/cameras: No DB connection.")
        return jsonify({"error": "Database connection failed"}), 503

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT CameraID FROM anpr_events WHERE CameraID IS NOT NULL AND CameraID != '' ORDER BY CameraID")
        cameras = [row[0] for row in cursor.fetchall()]
        cursor.close()
        early_logger.debug(f"/api/cameras: Found {len(cameras)} distinct cameras.")
        return jsonify({"cameras": cameras})
    except mysql.connector.Error as e:
        logger.error(f"Database error in /api/cameras: {e}", exc_info=True)
        early_logger.exception(f"/api/cameras: Database error: {e}")
        return jsonify({"error": "Database query failed"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in /api/cameras: {e}", exc_info=True)
        early_logger.exception(f"/api/cameras: Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/api/events/latest_timestamp', methods=['GET'])
def get_latest_event_timestamp():
    early_logger.debug("Route /api/events/latest_timestamp called.")
    conn = get_db_connection()
    if not conn:
        early_logger.error("/api/events/latest_timestamp: No DB connection.")
        return jsonify({"error": "Database connection failed"}), 503

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(Timestamp) FROM anpr_events")
        latest_ts_db = cursor.fetchone()[0]
        cursor.close()
        early_logger.debug(f"/api/events/latest_timestamp: DB responded with {latest_ts_db}")

        latest_timestamp_iso = None
        if latest_ts_db and isinstance(latest_ts_db, datetime.datetime):
            latest_timestamp_iso = latest_ts_db.isoformat(sep=' ', timespec='milliseconds')
            early_logger.debug(f"/api/events/latest_timestamp: Formatted timestamp {latest_timestamp_iso}")


        return jsonify({"latest_timestamp": latest_timestamp_iso})
    except mysql.connector.Error as e:
        logger.error(f"Database error in /api/events/latest_timestamp: {e}", exc_info=True)
        early_logger.exception(f"/api/events/latest_timestamp: Database error: {e}")
        return jsonify({"error": "Database query failed"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in /api/events/latest_timestamp: {e}", exc_info=True)
        early_logger.exception(f"/api/events/latest_timestamp: Unexpected error: {e}")
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    early_logger.info("Health check endpoint /health called.")
    db_connected = False
    table_exists = False
    conn = get_db_connection() # This will attempt to initialize if needed

    if conn and conn.is_connected():
        db_connected = True
        early_logger.info("Health check: Database connection appears to be established.")
        try:
            cursor = conn.cursor()
            early_logger.info(f"Health check: Executing query to check for anpr_events table in schema {DB_NAME}...")
            # Check if the anpr_events table exists
            cursor.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{DB_NAME}' AND table_name = 'anpr_events'")
            table_count = cursor.fetchone()[0]
            early_logger.info(f"Health check: information_schema.tables query returned count: {table_count}")
            if table_count == 1:
                table_exists = True
                early_logger.info("Health check: Table 'anpr_events' confirmed to exist.")
            else:
                logger.warning(f"Health check for anpr_web: Table 'anpr_events' does not exist in database '{DB_NAME}'.")
                early_logger.warning(f"Health check: Table 'anpr_events' does NOT exist in database '{DB_NAME}'.")
            cursor.close()
        except mysql.connector.Error as e:
            logger.error(f"Health check for anpr_web: Database error while checking for table: {e}")
            early_logger.exception(f"Health check: Database error while checking for anpr_events table: {e}")
            # If query fails, consider DB not fully healthy for this check, but connection itself might be ok
            # We will rely on table_exists being false.
        except Exception as e_gen:
            logger.error(f"Health check for anpr_web: Generic error while checking for table: {e_gen}")
            early_logger.exception(f"Health check: Generic error while checking for anpr_events table: {e_gen}")
            # Treat generic errors as a problem too
    else:
        early_logger.warning("Health check: Database connection is NOT established (conn is None or not connected).")


    if db_connected and table_exists:
        early_logger.info("Health check: Result -> Healthy (DB connected, table exists). Returning 200.")
        return jsonify({"status": "healthy", "database_connection": "ok", "anpr_events_table": "exists"}), 200
    elif db_connected and not table_exists:
        early_logger.warning("Health check: Result -> Unhealthy (DB connected, but table missing). Returning 503.")
        # This state might occur if anpr_db_manager hasn't created the table yet.
        # The service is up, DB is connected, but not fully ready for queries.
        return jsonify({"status": "unhealthy", "database_connection": "ok", "anpr_events_table": "missing"}), 503
    else: # db_connected is False
        early_logger.warning("Health check: Result -> Unhealthy (DB connection error). Returning 503.")
        return jsonify({"status": "unhealthy", "database_connection": "error", "anpr_events_table": "unknown"}), 503

def close_db_connection_on_exit():
    global DB_CONNECTION
    early_logger.info("close_db_connection_on_exit() called.")
    if DB_CONNECTION and DB_CONNECTION.is_connected():
        try:
            DB_CONNECTION.close()
            logger.info("MariaDB connection closed on application exit.")
            early_logger.info("Successfully closed MariaDB connection.")
        except Exception as e:
            logger.error(f"Error closing MariaDB connection: {e}")
            early_logger.exception(f"Error closing MariaDB connection: {e}")


if __name__ == '__main__':
    early_logger.info("Script executed with __name__ == '__main__'. This is for direct execution, not Gunicorn.")
    # Ensure DB is initialized at startup (or attempt to)
    early_logger.info("Attempting database initialization for direct execution mode...")
    if not initialize_database():
        logger.warning("Database initialization failed at startup. API might not function correctly for DB operations until DB is available.")
        early_logger.warning("Database initialization FAILED for direct execution mode.")
    else:
        early_logger.info("Database initialization SUCCEEDED for direct execution mode.")


    import atexit
    atexit.register(close_db_connection_on_exit)
    early_logger.info("Registered close_db_connection_on_exit with atexit.")

    server_port = int(os.getenv('FLASK_RUN_PORT', 5000)) # Default port 5000 for web UI
    logger.info(f"Starting ANPR Web UI Flask server on http://0.0.0.0:{server_port}...")
    early_logger.info(f"Attempting to start Flask development server on port {server_port}...")
    # When running with Gunicorn, Gunicorn handles worker management and binding.
    # This app.run() is primarily for direct execution (local development).
    try:
        app.run(host='0.0.0.0', port=server_port, debug=False) # debug=False for consistency with Gunicorn
        early_logger.info(f"Flask development server app.run() called.")
    except Exception as e:
        early_logger.exception(f"CRITICAL_ERROR_FLASK_RUN: app.run() failed for direct execution.")
        sys.exit(1)
else:
    early_logger.info("Script imported by Gunicorn (or other). __name__ is not '__main__'.")
    early_logger.info("Attempting database initialization for Gunicorn worker...")
    if not initialize_database():
        # Log heavily, but Gunicorn will manage the worker. Health check should fail.
        logger.critical("DATABASE INITIALIZATION FAILED FOR GUNICORN WORKER. THIS WORKER WILL LIKELY BE UNHEALTHY.")
        early_logger.critical("DATABASE INITIALIZATION FAILED FOR GUNICORN WORKER. THIS WORKER WILL LIKELY BE UNHEALTHY.")
    else:
        early_logger.info("Database initialization SUCCEEDED for Gunicorn worker.")

    import atexit
    atexit.register(close_db_connection_on_exit)
    early_logger.info("Registered close_db_connection_on_exit with atexit for Gunicorn worker.")
