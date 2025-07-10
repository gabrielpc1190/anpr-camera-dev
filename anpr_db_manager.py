import time, sys, logging, os, re, configparser, uuid
import mysql.connector

# --- Configuration Loading ---
config = configparser.ConfigParser(interpolation=None)
# Assuming config.ini will be in the same directory as anpr_poc.py or specified via an absolute path
# For now, let's assume it's relative to where the script is run or we'll need to adjust this path.
# For a robust solution, consider passing the config path or making it configurable.
# For this PoC, we'll assume it's in the same directory as anpr_poc.py for simplicity.
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
if not os.path.exists(config_path):
    # Fallback for development/testing if config.ini is not in the same dir
    config_path = '/app/config.ini' # Path used in anpr-camera-listener Docker setup
    if not os.path.exists(config_path):
        logging.error(f"config.ini not found at {os.path.dirname(os.path.abspath(__file__))} or /app/. Please ensure it exists.")
        sys.exit(1)

config.read(config_path)

IMAGE_DIR = config.get('Paths', 'ImageDirectory', fallback=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'capturas'))
LOG_FILE = config.get('General', 'LogFile', fallback=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'anpr_events.log'))

# Ensure IMAGE_DIR exists
os.makedirs(IMAGE_DIR, exist_ok=True)

# --- Database Configuration ---
DB_HOST = os.getenv('DB_HOST', 'mariadb')
DB_USER = os.getenv('MYSQL_USER', 'anpr_user')
DB_PASSWORD = os.getenv('MYSQL_PASSWORD')
DB_NAME = os.getenv('MYSQL_DATABASE', 'anpr_events')

# --- Logging Setup ---
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(funcName)s: %(message)s')
# Ensure log directory exists before setting up handler
if LOG_FILE:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)
logger = logging.getLogger(__name__) # Use __name__ for logger
logger.setLevel(logging.INFO)
if not logger.handlers: # Prevent adding multiple handlers if already configured
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

DB_CONNECTION = None

def sanitize_filename(name_part):
    if not name_part or name_part == "N/A": return "NO_PLATE"
    return re.sub(r'[\\/*?:"<>|]', "_", name_part)

def initialize_database():
    global DB_CONNECTION
    attempts = 0
    while attempts < 10:
        try:
            conn = mysql.connector.connect(
                host=DB_HOST,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME
            )
            if conn.is_connected():
                logger.info("Successfully connected to MariaDB database.")
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
                    cursor.close()
                    logger.info("Table anpr_events ensured.")
                except mysql.connector.Error as err_table:
                    logger.error(f"Failed to create/ensure anpr_events table: {err_table}")
                return
        except mysql.connector.Error as e:
            logger.warning(f"Failed to connect to database: {e}. Retrying in 5 seconds...")
            attempts += 1
            time.sleep(5)
    
    logger.critical("Could not connect to the database after multiple attempts. Exiting.")
    sys.exit(1)

def insert_anpr_event_db(event_data):
    if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
        logger.warning("DB_CONNECTION is not available. Attempting to reconnect...")
        initialize_database()
        if DB_CONNECTION is None or not DB_CONNECTION.is_connected():
            logger.error("Failed to re-establish database connection. Cannot insert event.")
            return

    sql = '''INSERT INTO anpr_events (Timestamp, PlateNumber, EventType, CameraID, VehicleType, VehicleColor, PlateColor, ImageFilename, DrivingDirection)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)'''
    try:
        plate_num_cleaned = event_data.get("PlateNumber", "").strip()
        data_tuple = (
            event_data.get("Timestamp"), plate_num_cleaned, event_data.get("EventType"),
            event_data.get("CameraID"), event_data.get("VehicleType"),
            event_data.get("VehicleColor"), event_data.get("PlateColor"),
            event_data.get("ImageFilename"), event_data.get("DrivingDirection")
        )

        cursor = DB_CONNECTION.cursor()
        cursor.execute(sql, data_tuple)
        DB_CONNECTION.commit()
        logger.info(f"Event for plate '{plate_num_cleaned}' inserted successfully. DB Row ID: {cursor.lastrowid}")
        cursor.close()

    except mysql.connector.Error as e:
        logger.error(f"Error inserting into DB: {e}. Data: {event_data}", exc_info=True)

def save_image(image_buffer, camera_id, timestamp_utc, plate_number):
    if image_buffer and len(image_buffer) > 0:
        image_uuid = uuid.uuid4()
        ts_for_fn = f"{timestamp_utc.dwYear:04d}{timestamp_utc.dwMonth:02d}{timestamp_utc.dwDay:02d}_{timestamp_utc.dwHour:02d}{timestamp_utc.dwMinute:02d}{timestamp_utc.dwSecond:02d}"
        
        # Sanitize plate number for filename
        sanitized_plate = sanitize_filename(plate_number)
        
        image_basename = f"{ts_for_fn}_{camera_id}_{sanitized_plate}_{image_uuid}.jpg"
        img_fn_with_path = os.path.join(IMAGE_DIR, image_basename)
        try:
            with open(img_fn_with_path, "wb") as f_img:
                f_img.write(image_buffer)
            logger.info(f"Image saved: {img_fn_with_path}")
            return image_basename
        except IOError as img_e:
            logger.error(f"Error saving image {img_fn_with_path}: {img_e}")
    return None

def close_db_connection():
    global DB_CONNECTION
    if DB_CONNECTION and DB_CONNECTION.is_connected():
        DB_CONNECTION.close()
        logger.info("MariaDB connection closed.")
        DB_CONNECTION = None
