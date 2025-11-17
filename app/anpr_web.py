import os, logging, sys
import requests
from flask import Flask, jsonify, request, send_from_directory, abort

import configparser

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration ---
config = configparser.ConfigParser(interpolation=None)
config.read('/app/config.ini')

IMAGE_DIR = os.getenv('IMAGE_DIR', '/app/anpr_images')
DB_MANAGER_API_URL = os.getenv('DB_MANAGER_API_URL', 'http://anpr-db-manager:5001')
LOG_DIR = os.getenv('LOG_DIR', '/app/logs')
LOG_FILE = os.path.join(LOG_DIR, 'anpr_web.log')

os.makedirs(LOG_DIR, exist_ok=True)

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

# --- API Endpoints (Proxy to anpr_db_manager) ---

@app.route('/api/events', methods=['GET'])
def proxy_events():
    query_params = request.query_string.decode('utf-8')
    try:
        backend_response = requests.get(f"{DB_MANAGER_API_URL}/api/events?{query_params}", timeout=10)
        backend_response.raise_for_status()
        
        data = backend_response.json()
        response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
        return response, backend_response.status_code

    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying /api/events to db_manager: {e}")
        abort(502, description="Error connecting to the database service.")

@app.route('/api/cameras', methods=['GET'])
def proxy_cameras():
    try:
        backend_response = requests.get(f"{DB_MANAGER_API_URL}/api/cameras", timeout=5)
        backend_response.raise_for_status()

        data = backend_response.json()
        response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
        return response, backend_response.status_code

    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying /api/cameras to db_manager: {e}")
        abort(502, description="Error connecting to the database service.")

@app.route('/api/events/latest_timestamp', methods=['GET'])
def proxy_latest_timestamp():
    query_params = request.query_string.decode('utf-8')
    try:
        backend_response = requests.get(f"{DB_MANAGER_API_URL}/api/events/latest_timestamp?{query_params}", timeout=5)
        backend_response.raise_for_status()

        data = backend_response.json()
        response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
        return response, backend_response.status_code

    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying /api/events/latest_timestamp to db_manager: {e}")
        abort(502, description="Error connecting to the database service.")

# --- Static File Serving ---

@app.route('/images/<path:filename>')
def serve_image(filename):
    if '..' in filename or filename.startswith('/'):
        abort(404)
    return send_from_directory(IMAGE_DIR, filename)

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

# --- Health Check ---

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    server_port = int(os.getenv('FLASK_RUN_PORT', 5000))
    app.run(host='0.0.0.0', port=server_port, debug=False)