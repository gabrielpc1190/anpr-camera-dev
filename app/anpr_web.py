import os, logging, sys
import requests
from flask import Flask, jsonify, request, send_from_directory, abort, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import bcrypt
import configparser

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'a_secure_secret_key')
db_host = os.getenv('MYSQL_HOST', 'mariadb')
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{os.getenv('MYSQL_PASSWORD')}@{db_host}/{os.getenv('MYSQL_DATABASE')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

    def set_password(self, password):
        self.password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password.encode('utf-8'))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/api/events', methods=['GET'])
@login_required
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
@login_required
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
@login_required
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
@login_required
def serve_image(filename):
    if '..' in filename or filename.startswith('/'):
        abort(404)
    return send_from_directory(IMAGE_DIR, filename)

@app.route('/')
@login_required
def index():
    return send_from_directory('templates', 'index.html')

# --- Health Check ---

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    server_port = int(os.getenv('FLASK_RUN_PORT', 5000))
    app.run(host='0.0.0.0', port=server_port, debug=False)