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
    CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini') # Fallback for local

if not os.path.exists(CONFIG_FILE_PATH):
    logging.basicConfig(level=logging.ERROR)
    logging.error(f"CRITICAL: config.ini not found at /app/config.ini or local path. Please ensure it exists.")
    sys.exit(1)
config.read(CONFIG_FILE_PATH)

# --- Global Variables & Paths from Config ---
IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images') # Default for Docker
LOG_FILE_PATH = config.get('General', 'LogFile', fallback='/app/logs/anpr_web.log') # Default for Docker

# Ensure log directory exists
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
DB_PASSWORD = os.getenv('MYSQL_PASSWORD')
DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')

if not DB_PASSWORD:
    logger.critical("MYSQL_PASSWORD environment variable not set. Cannot connect to database.")
    sys.exit(1)

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
            initialize_database()
        else:
            DB_CONNECTION.ping(reconnect=True, attempts=3, delay=1)
            logger.debug("Database connection is active.")
        return DB_CONNECTION
    except mysql.connector.Error as e:
        logger.error(f"Database connection check failed: {e}. Attempting to re-initialize.")
        initialize_database()
        return DB_CONNECTION

# --- Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    logger.debug(f"Attempting to serve image: {filename} from directory: {IMAGE_DIR}")
    return send_from_directory(IMAGE_DIR, filename)

@app.route('/api/events', methods=['GET'])
def get_events():
    conn = get_db_connection()
    if not conn or not conn.is_connected():
        logger.error("No database connection for /api/events")
        return jsonify({"error": "Database connection failed"}), 500

    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        offset = (page - 1) * limit

        # Filters
        filters = []
        params = []

        # String fields
        for field in ['plate', 'camera_id', 'vehicle_type', 'vehicle_color', 'driving_direction']:
            value = request.args.get(field)
            if value:
                # Map 'plate' from query to 'PlateNumber' in DB
                db_field = 'PlateNumber' if field == 'plate' else field.replace('_', '') # Simple snake_case to PascalCase
                # For exact match on CameraID, VehicleType, VehicleColor, DrivingDirection
                if field in ['camera_id', 'vehicle_type', 'vehicle_color', 'driving_direction']:
                     # Need to map field names from JS (snake_case) to DB (PascalCase)
                    db_field_map = {
                        'camera_id': 'CameraID',
                        'vehicle_type': 'VehicleType',
                        'vehicle_color': 'VehicleColor',
                        'driving_direction': 'DrivingDirection'
                    }
                    filters.append(f"{db_field_map.get(field, field)} = %s")
                else: # For plate number, use LIKE
                    filters.append(f"{db_field} LIKE %s")
                    value = f"%{value}%"
                params.append(value)

        # Date fields
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if start_date_str:
            filters.append("Timestamp >= %s")
            params.append(start_date_str)
        if end_date_str:
            filters.append("Timestamp <= %s")
            params.append(end_date_str)

        where_clause = " AND ".join(filters) if filters else "1=1"

        # Count total events for pagination
        count_query = f"SELECT COUNT(*) FROM anpr_events WHERE {where_clause}"
        cursor = conn.cursor()
        cursor.execute(count_query, tuple(params))
        total_events = cursor.fetchone()[0]
        total_pages = math.ceil(total_events / limit)

        # Fetch events for the current page
        query = f"""
            SELECT Timestamp, PlateNumber, EventType, CameraID, VehicleType, VehicleColor, PlateColor, ImageFilename, DrivingDirection, VehicleSpeed, Lane
            FROM anpr_events
            WHERE {where_clause}
            ORDER BY Timestamp DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query, tuple(params + [limit, offset]))

        events = []
        columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            event = dict(zip(columns, row))
            # Format Timestamp as ISO string or desired format
            if isinstance(event.get('Timestamp'), datetime.datetime):
                event['Timestamp'] = event['Timestamp'].isoformat(sep=' ', timespec='milliseconds')
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
    if not conn or not conn.is_connected():
        return jsonify({"error": "Database connection failed"}), 500

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
    if not conn or not conn.is_connected():
        return jsonify({"error": "Database connection failed"}), 500

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

def close_db_connection_on_exit():
    global DB_CONNECTION
    if DB_CONNECTION and DB_CONNECTION.is_connected():
        DB_CONNECTION.close()
        logger.info("MariaDB connection closed on application exit.")

if __name__ == '__main__':
    if not initialize_database():
        logger.warning("Database initialization failed. API might not function correctly for DB operations.")

    import atexit
    atexit.register(close_db_connection_on_exit)

    server_port = int(os.getenv('FLASK_RUN_PORT', 5000)) # Default port 5000 for web UI
    logger.info(f"Starting ANPR Web UI Flask server on port {server_port}...")
    app.run(host='0.0.0.0', port=server_port, debug=False) # debug=False for production-like
