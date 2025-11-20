import os
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from app.models import db, User

app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev_key_please_change_in_prod')
app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+mysqlconnector://{os.getenv('MYSQL_USER', 'anpr_user')}:"
    f"{os.getenv('MYSQL_PASSWORD')}@"
    f"{os.getenv('DB_HOST', 'mariadb')}/"
    f"{os.getenv('MYSQL_DATABASE', 'anpr_events')}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# DB Manager API URL
DB_MANAGER_API_URL = os.getenv('DB_MANAGER_API_URL', 'http://localhost:5001')

# --- Initialize Extensions ---
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Routes ---

@app.route('/health', methods=['GET'])
def health_check():
    """Public health check endpoint."""
    return jsonify({"status": "healthy"}), 200

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid username or password')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

# --- API Proxy Routes ---

@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def api_proxy(path):
    """Proxy API requests to the DB Manager service."""
    url = f"{DB_MANAGER_API_URL}/api/{path}"
    
    # Forward query parameters
    if request.query_string:
        url += f"?{request.query_string.decode('utf-8')}"
    
    try:
        # Forward the request to DB Manager
        if request.method == 'GET':
            response = requests.get(url, timeout=10)
        elif request.method == 'POST':
            response = requests.post(url, json=request.get_json(), timeout=10)
        elif request.method == 'PUT':
            response = requests.put(url, json=request.get_json(), timeout=10)
        elif request.method == 'DELETE':
            response = requests.delete(url, timeout=10)
        
        # Return the response from DB Manager
        return response.content, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'application/json')}
    
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to connect to DB Manager: {str(e)}"}), 503

@app.route('/images/<path:filename>')
@login_required
def serve_image(filename):
    """Serve images from the anpr_images directory."""
    images_dir = '/app/anpr_images'
    return send_from_directory(images_dir, filename)

# --- Database Initialization ---
with app.app_context():
    # Create tables if they don't exist (specifically the 'user' table)
    db.create_all()

if __name__ == '__main__':
    server_port = int(os.getenv('FLASK_RUN_PORT', 5000))
    app.run(host='0.0.0.0', port=server_port, debug=False)