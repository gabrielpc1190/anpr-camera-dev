import time, sys, logging, os, re, configparser, uuid, json
from datetime import datetime
import mysql.connector
from flask import Flask, jsonify, request, abort
from werkzeug.utils import secure_filename
from math import ceil

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration Loading ---
config = configparser.ConfigParser(interpolation=None)
config_path = '/app/config.ini'
if not os.path.exists(config_path):
    alt_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
    if os.path.exists(alt_config_path):
        config_path = alt_config_path
    else:
        logging.basicConfig(level=logging.ERROR)
        logger_fallback = logging.getLogger(__name__)
        logger_fallback.error(f"CRITICAL: config.ini not found at /app/config.ini or {alt_config_path}.")
        sys.exit(1)
config.read(config_path)

IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback='/app/anpr_images')
LOG_DIR = config.get('General', 'LogDirectory', fallback='/app/logs')
LOG_FILE = os.path.join(LOG_DIR, 'anpr_db_manager.log')

os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# --- Database Configuration ---
DB_HOST = os.getenv('DB_HOST', 'mariadb')
DB_USER = os.getenv('MYSQL_USER', 'anpr_user')
DB_PASSWORD = os.getenv('MYSQL_PASSWORD')
DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')

# --- Logging Setup ---
LOG_LEVEL_MAP = {
    '0': logging.ERROR, '1': logging.WARNING, '2': logging.INFO, '3': logging.DEBUG
}
log_level_str = config.get('General', 'LogLevel', fallback='2')
log_level = LOG_LEVEL_MAP.get(log_level_str, logging.INFO)

log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(module)s:%(funcName)s:%(lineno)d] %(message)s')
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger = logging.getLogger(__name__)
app.logger.handlers = []; app.logger.propagate = False
for handler in [file_handler, console_handler]:
    logger.addHandler(handler)
    app.logger.addHandler(handler)
logger.setLevel(log_level)
app.logger.setLevel(log_level)

if not DB_PASSWORD:
    logger.critical("CRITICAL: MYSQL_PASSWORD environment variable not set.")
    sys.exit(1)

def sanitize_filename(filename):
    sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '', filename)
    return sanitized[:200]

TABLE_INITIALIZED = False

def initialize_database():
    global TABLE_INITIALIZED
    if TABLE_INITIALIZED:
        return True
    conn = None
    try:
        conn = mysql.connector.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME
        )
        cursor = conn.cursor()
        logger.info("Attempting to create anpr_events table if it does not exist...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anpr_events (
                id INT AUTO_INCREMENT PRIMARY KEY, plate_number VARCHAR(255) NOT NULL,
                camera_id VARCHAR(255), timestamp DATETIME NOT NULL, image_filename VARCHAR(255),
                confidence FLOAT, processed_data JSON, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cursor.close(); conn.close()
        TABLE_INITIALIZED = True
        logger.info("Database table 'anpr_events' ensured to exist.")
        return True
    except mysql.connector.Error as err:
        logger.error(f"Error initializing database: {err}", exc_info=True)
        if conn: conn.close()
        return False

def get_db_connection():
    try:
        if not TABLE_INITIALIZED:
            initialize_database()
        conn = mysql.connector.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, autocommit=False
        )
        logger.debug("Successfully established new database connection.")
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Failed to connect to database: {err}")
        return None

@app.route('/event', methods=['POST'])
def receive_event():
    conn = get_db_connection()
    if not conn: return jsonify({"status": "error", "message": "Database connection unavailable"}), 503
    if 'event_data' not in request.form:
        conn.close()
        return jsonify({"status": "error", "message": "Missing 'event_data' in form"}), 400
    try:
        event_data = json.loads(request.form['event_data'])
    except json.JSONDecodeError:
        conn.close()
        return jsonify({"status": "error", "message": "Invalid JSON in event_data"}), 400
    image_file = request.files.get('image')
    image_filename = None
    if image_file:
        if image_file.filename == '':
            conn.close()
            return jsonify({"status": "error", "message": "Received image file with no name"}), 400
        timestamp = event_data.get("Timestamp", "notime").replace(":", "-")
        plate = sanitize_filename(event_data.get("PlateNumber", "noplate"))
        cam_id = sanitize_filename(event_data.get("CameraID", "nocam"))
        unique_id = str(uuid.uuid4())[:8]
        ext = os.path.splitext(secure_filename(image_file.filename))[1] or '.jpg'
        image_filename = f"{timestamp}_{cam_id}_{plate}_{unique_id}{ext}"
        filepath = os.path.join(IMAGE_DIR, image_filename)
        try:
            image_file.save(filepath)
            logger.info(f"Image saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save image file to {filepath}: {e}", exc_info=True)
            conn.close()
            return jsonify({"status": "error", "message": "Failed to save image"}), 500
    try:
        if insert_anpr_event_db(event_data, image_filename, conn):
            return jsonify({"status": "success", "message": "Event and image processed"}), 201
        else:
            return jsonify({"status": "error", "message": "Failed to insert event into database"}), 500
    finally:
        if conn and conn.is_connected(): conn.close()

def insert_anpr_event_db(event_data, image_filename, db_conn):
    cursor = None
    try:
        cursor = db_conn.cursor()
        plate_number = event_data.get("PlateNumber")
        camera_id = event_data.get("CameraID")
        timestamp_str = event_data.get("EventTimeUTC")
        confidence = event_data.get("Confidence", None)
        if timestamp_str is None:
            logger.error("Timestamp string is None. Cannot convert to datetime.")
            return False
        try:
            timestamp_obj = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            logger.error(f"Invalid timestamp format: {timestamp_str}")
            return False
        sql = """
            INSERT INTO anpr_events (plate_number, camera_id, timestamp, image_filename, confidence, processed_data)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        val = (plate_number, camera_id, timestamp_obj, image_filename, confidence, json.dumps(event_data))
        cursor.execute(sql, val)
        last_id = cursor.lastrowid
        db_conn.commit()
        logger.info(f"Event for plate '{plate_number}' inserted successfully. DB Row ID: {last_id}")
        return True
    except mysql.connector.Error as err:
        logger.error(f"Error inserting event into database: {err}", exc_info=True)
        db_conn.rollback()
        return False
    finally:
        if cursor: cursor.close()

@app.route('/api/events', methods=['GET'])
def get_events():
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    plate_number = request.args.get('plate_number', type=str)
    camera_id = request.args.get('camera_id', type=str)
    start_date_str = request.args.get('start_date', type=str)
    end_date_str = request.args.get('end_date', type=str)
    offset = (page - 1) * limit
    query_params, where_clauses = [], []
    base_query = "FROM anpr_events"
    if plate_number:
        where_clauses.append("plate_number LIKE %s")
        query_params.append(f"%{plate_number}%")
    if camera_id:
        where_clauses.append("camera_id = %s")
        query_params.append(camera_id)
    if start_date_str:
        where_clauses.append("DATE(timestamp) >= %s")
        query_params.append(start_date_str)
    if end_date_str:
        where_clauses.append("DATE(timestamp) <= %s")
        query_params.append(end_date_str)
    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    count_query = "SELECT COUNT(*) " + base_query
    sql_query = f"SELECT id, plate_number, camera_id, timestamp, image_filename, confidence, processed_data {base_query}"
    try:
        count_cursor = conn.cursor()
        count_cursor.execute(count_query, query_params)
        total_events = count_cursor.fetchone()[0]
        count_cursor.close()
        total_pages = ceil(total_events / limit) if limit > 0 else 0
        data_cursor = conn.cursor(dictionary=True)
        sql_query += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
        data_query_params = query_params.copy()
        data_query_params.extend([limit, offset])
        data_cursor.execute(sql_query, data_query_params)
        events = data_cursor.fetchall()
        data_cursor.close()
        for event in events:
            if event.get('timestamp'): event['timestamp'] = event['timestamp'].isoformat()
            if event.get('processed_data'):
                try: event['processed_data'] = json.loads(event['processed_data'])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Could not parse processed_data for event ID {event.get('id')}")
                    event['processed_data'] = {}
        return jsonify({
            "events": events, "total_pages": total_pages,
            "current_page": page, "total_events": total_events
        })
    except mysql.connector.Error as err:
        logger.error(f"Error fetching events: {err}", exc_info=True)
        return jsonify({"status": "error", "message": "Error querying database"}), 500
    finally:
        if conn and conn.is_connected(): conn.close()

@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT camera_id FROM anpr_events WHERE camera_id IS NOT NULL AND camera_id != '' ORDER BY camera_id")
        cameras = [row[0] for row in cursor.fetchall()]
        return jsonify({"cameras": cameras})
    except mysql.connector.Error as err:
        logger.error(f"Error fetching camera IDs: {err}", exc_info=True)
        return jsonify({"cameras": []}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

@app.route('/api/events/latest_timestamp', methods=['GET'])
def get_latest_timestamp():
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    
    since_timestamp_str = request.args.get('since', None)
    new_events_count = 0
    
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(timestamp) FROM anpr_events")
        latest_timestamp = cursor.fetchone()[0]

        if since_timestamp_str and latest_timestamp:
            try:
                since_timestamp_obj = datetime.fromisoformat(since_timestamp_str.replace('Z', '+00:00'))
                cursor.execute(
                    "SELECT COUNT(*) FROM anpr_events WHERE timestamp > %s", 
                    (since_timestamp_obj,)
                )
                new_events_count = cursor.fetchone()[0]
            except (ValueError, TypeError):
                logger.warning(f"Invalid 'since' timestamp format received: {since_timestamp_str}")

        return jsonify({
            "latest_timestamp": latest_timestamp.isoformat() if latest_timestamp else None,
            "new_events_count": new_events_count
        })
    except mysql.connector.Error as err:
        logger.error(f"Error fetching latest timestamp: {err}", exc_info=True)
        return jsonify({"latest_timestamp": None, "new_events_count": 0}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()

@app.route('/health', methods=['GET'])
def health_check():
    conn = None
    try:
        conn = get_db_connection()
        if conn and conn.is_connected():
            return jsonify({"status": "ok", "message": "Database connection successful"}), 200
        else:
            return jsonify({"status": "error", "message": "Database connection failed"}), 503
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Health check failed"}), 500
    finally:
        if conn and conn.is_connected(): conn.close()

if __name__ == '__main__':
    initialize_database()
    server_port = int(os.getenv('FLASK_RUN_PORT', 5001))
    app.run(host='0.0.0.0', port=server_port, debug=False)