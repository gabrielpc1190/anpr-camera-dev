import os
from datetime import timedelta
from functools import wraps
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_session import Session
from app.models import db, User
from urllib.parse import urlparse

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

# --- Session Configuration (server-side, DB-backed) ---
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_SQLALCHEMY'] = db
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'anpr_session:'

# DB Manager API URL
DB_MANAGER_API_URL = os.getenv('DB_MANAGER_API_URL', 'http://localhost:5001')

# --- Initialize Extensions ---
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize server-side sessions
sess = Session(app)

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Decorators ---
def admin_required(f):
    """Decorator that ensures the current user is an admin."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

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
            session.permanent = True
            next_page = request.args.get('next')
            if not next_page or urlparse(next_page).netloc != '':
                next_page = url_for('index')
            return redirect(next_page)
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

# --- Admin Panel ---

@app.route('/admin')
@admin_required
def admin_panel():
    return render_template('admin.html')

@app.route('/admin/sessions', methods=['GET'])
@admin_required
def list_sessions():
    """List all active server-side sessions."""
    session_model = app.config.get('SESSION_SQLALCHEMY_TABLE', 'sessions')
    try:
        result = db.session.execute(
            db.text(f"SELECT id, session_id, expiry FROM {session_model}")
        )
        sessions_list = []
        import msgspec
        for row in result:
            session_data = {}
            try:
                # Try to decode session data to get user info
                raw = db.session.execute(
                    db.text(f"SELECT data FROM {session_model} WHERE id = :id"),
                    {"id": row.id}
                ).fetchone()
                if raw and raw.data:
                    decoded = msgspec.msgpack.decode(raw.data)
                    user_id = decoded.get('_user_id')
                    if user_id:
                        user = User.query.get(int(user_id))
                        session_data['username'] = user.username if user else f'Unknown (ID:{user_id})'
                        session_data['role'] = user.role if user else 'unknown'
                    else:
                        session_data['username'] = 'Anonymous'
                        session_data['role'] = '-'
            except Exception:
                session_data['username'] = 'Unknown'
                session_data['role'] = '-'

            sessions_list.append({
                'id': row.id,
                'session_id': row.session_id[:16] + '...',
                'username': session_data.get('username', 'Unknown'),
                'role': session_data.get('role', '-'),
                'expiry': row.expiry.isoformat() if row.expiry else None,
            })

        return jsonify({'sessions': sessions_list, 'count': len(sessions_list)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/sessions/<int:session_id>', methods=['DELETE'])
@admin_required
def revoke_session(session_id):
    """Revoke a single session by its DB id."""
    session_model = app.config.get('SESSION_SQLALCHEMY_TABLE', 'sessions')
    try:
        db.session.execute(
            db.text(f"DELETE FROM {session_model} WHERE id = :id"),
            {"id": session_id}
        )
        db.session.commit()
        return jsonify({'status': 'ok', 'message': 'Session revoked'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/admin/sessions/revoke-all', methods=['POST'])
@admin_required
def revoke_all_sessions():
    """Revoke all sessions (forces everyone to re-login)."""
    session_model = app.config.get('SESSION_SQLALCHEMY_TABLE', 'sessions')
    try:
        result = db.session.execute(db.text(f"DELETE FROM {session_model}"))
        db.session.commit()
        return jsonify({'status': 'ok', 'message': f'All sessions revoked ({result.rowcount} removed)'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# --- Admin User Management (viewer users only) ---

@app.route('/admin/users', methods=['GET'])
@admin_required
def list_users():
    """List all viewer users (admin users hidden from web UI)."""
    users = User.query.filter_by(role='viewer').all()
    return jsonify({
        'users': [{'id': u.id, 'username': u.username, 'role': u.role} for u in users],
        'count': len(users)
    })

@app.route('/admin/users', methods=['POST'])
@admin_required
def create_viewer_user():
    """Create a new viewer user."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': f'User "{username}" already exists'}), 409

    user = User(username=username, role='viewer')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({'status': 'ok', 'message': f'Viewer user "{username}" created', 'user': {'id': user.id, 'username': user.username, 'role': user.role}}), 201

@app.route('/admin/users/<int:user_id>', methods=['PUT'])
@admin_required
def update_viewer_user(user_id):
    """Update a viewer user's username."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.is_admin:
        return jsonify({'error': 'Cannot modify admin users from the web interface. Use the CLI.'}), 403

    data = request.get_json()
    new_username = data.get('username', '').strip() if data else ''
    if not new_username:
        return jsonify({'error': 'New username is required'}), 400

    existing = User.query.filter_by(username=new_username).first()
    if existing and existing.id != user_id:
        return jsonify({'error': f'Username "{new_username}" is already taken'}), 409

    user.username = new_username
    db.session.commit()
    return jsonify({'status': 'ok', 'message': f'Username updated to "{new_username}"'})

@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def reset_viewer_password(user_id):
    """Reset a viewer user's password."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.is_admin:
        return jsonify({'error': 'Cannot reset admin passwords from the web interface. Use the CLI.'}), 403

    data = request.get_json()
    new_password = data.get('password', '') if data else ''
    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'New password must be at least 6 characters'}), 400

    user.set_password(new_password)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': f'Password reset for "{user.username}"'})

@app.route('/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_viewer_user(user_id):
    """Delete a viewer user."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.is_admin:
        return jsonify({'error': 'Cannot delete admin users from the web interface. Use the CLI.'}), 403

    username = user.username
    db.session.delete(user)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': f'User "{username}" deleted'})

# --- API Proxy Routes ---

@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def api_proxy(path):
    """Proxy API requests to the DB Manager service.
    Viewer users are restricted to GET requests only.
    """
    # Role-based restriction: viewers can only read
    if not current_user.is_admin and request.method != 'GET':
        return jsonify({"error": "Permission denied. Viewer accounts are read-only."}), 403

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
    # Create tables if they don't exist (user table + sessions table)
    db.create_all()

    # Add 'role' column if it doesn't exist (ALTER TABLE for existing DBs)
    try:
        db.session.execute(db.text(
            "ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'viewer'"
        ))
        db.session.commit()
        print("Added 'role' column to user table.")
    except Exception:
        db.session.rollback()
        # Column already exists, no action needed




if __name__ == '__main__':
    server_port = int(os.getenv('FLASK_RUN_PORT', 5000))
    app.run(host='0.0.0.0', port=server_port, debug=False)
