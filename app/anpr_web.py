import os
import subprocess
import tempfile
from datetime import timedelta
from functools import wraps
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session, make_response, Response, current_app
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_session import Session
from app.models import db, User
from urllib.parse import urlparse
from werkzeug.utils import safe_join
import json

# --- i18n: load translations at startup (fail-fast if missing/malformed) ---
TRANSLATIONS = {}
for _lang in ('es', 'en'):
    with open(f'/app/app/translations/{_lang}.json', encoding='utf-8') as _f:
        TRANSLATIONS[_lang] = json.load(_f)
SUPPORTED_LANGS = ('es', 'en')
DEFAULT_LANG = 'es'

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

# --- Helpers ---
def get_allowed_camera_ids(user):
    """Return the list of camera_ids a viewer can access.

    Returns:
        None  -> admin (no filter)
        []    -> viewer without any group assignments (sees nothing)
        [int, ...] -> viewer with assignments
    """
    if user is None or not user.is_authenticated:
        return []
    if user.is_admin:
        return None
    result = db.session.execute(
        db.text(
            "SELECT DISTINCT cgm.camera_id "
            "FROM camera_group_members cgm "
            "JOIN user_camera_groups ucg ON ucg.group_id = cgm.group_id "
            "WHERE ucg.user_id = :uid"
        ),
        {"uid": user.id}
    )
    return sorted(int(row[0]) for row in result)

def get_lang():
    """Return the current language code from the cookie, validated against SUPPORTED_LANGS."""
    lang = request.cookies.get('lang', DEFAULT_LANG)
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def t(key):
    """Translate a key using the current language. Returns the key itself if not found
    (intentional: makes missing translations visible in UI for fast QA detection)."""
    return TRANSLATIONS[get_lang()].get(key, key)


@app.context_processor
def inject_i18n():
    """Make t(), current_lang and translations_json available in every template."""
    lang = get_lang()
    return {
        't': t,
        'current_lang': lang,
        'translations_json': json.dumps(TRANSLATIONS[lang]),
    }


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

@app.route('/set-lang/<lang>')
def set_lang(lang):
    """Set the user's preferred language via cookie. Validates next_url against open redirect."""
    if lang not in SUPPORTED_LANGS:
        abort(400)
    next_url = request.args.get('next', request.referrer or url_for('index'))
    # Prevent open redirect: only allow relative paths on the same host
    if urlparse(next_url).netloc != '':
        next_url = url_for('index')
    resp = make_response(redirect(next_url))
    resp.set_cookie('lang', lang, max_age=365*24*3600, samesite='Lax')
    return resp


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
            
            # Capture IP into the session dict. flask-session writes the session
            # row to the DB during save_session() at the end of the request, so
            # storing the IP here lets it be persisted atomically with the rest of
            # the session data. Previously a direct UPDATE to the ip_address
            # column ran inside the view before flask-session saved the row,
            # causing a race where many sessions ended up with NULL ip_address.
            # Handle potential proxies like Cloudflare.
            ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr)
            if ip_addr and ',' in ip_addr:
                ip_addr = ip_addr.split(',')[0].strip()
            session['ip_address'] = ip_addr

            next_page = request.args.get('next')
            if not next_page or urlparse(next_page).netloc != '':
                next_page = url_for('index')
            return redirect(next_page)
        else:
            flash(t('login.invalid'))

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
    current_sid = getattr(session, 'sid', None)
    try:
        result = db.session.execute(
            db.text(f"SELECT id, session_id, expiry, ip_address FROM {session_model}")
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
                        
                        # Correctly handle prefix for is_current check
                        prefix = app.config.get('SESSION_KEY_PREFIX', '')
                        full_sid = f"{prefix}{current_sid}" if current_sid else None
                        
                        # Prefer the IP captured in session data (race-free since
                        # flask-session writes it atomically). Fall back to the
                        # ip_address column for historical sessions saved by the
                        # old direct-UPDATE code path.
                        ip_from_session = decoded.get('ip_address')
                        ip_from_column = row.ip_address if hasattr(row, 'ip_address') else None
                        sessions_list.append({
                            'id': row.id,
                            'session_id': row.session_id[:16] + '...',
                            'username': session_data.get('username', 'Unknown'),
                            'role': session_data.get('role', '-'),
                            'ip_address': ip_from_session or ip_from_column,
                            'expiry': row.expiry.isoformat() if row.expiry else None,
                            'is_current': row.session_id == full_sid
                        })
            except Exception:
                pass

        return jsonify({'sessions': sessions_list, 'count': len(sessions_list)})
    except Exception as e:
        return jsonify({'error': t('backend.error.internal').format(error=str(e))}), 500

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
        return jsonify({'status': 'ok', 'message': t('backend.success.session_revoked')})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': t('backend.error.internal').format(error=str(e))}), 500

@app.route('/admin/sessions/revoke-all', methods=['POST'])
@admin_required
def revoke_all_sessions():
    """Revoke all sessions EXCEPT the current one."""
    session_model = app.config.get('SESSION_SQLALCHEMY_TABLE', 'sessions')
    current_sid = getattr(session, 'sid', None)
    try:
        if current_sid:
            result = db.session.execute(
                db.text(f"DELETE FROM {session_model} WHERE session_id != :sid"),
                {"sid": current_sid}
            )
        else:
            result = db.session.execute(db.text(f"DELETE FROM {session_model}"))
        db.session.commit()
        return jsonify({'status': 'ok', 'message': t('backend.success.sessions_revoked').format(count=result.rowcount)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': t('backend.error.internal').format(error=str(e))}), 500

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

def is_password_strong(password):
    """Check if password meets complexity requirements:
    - Minimum 10 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    """
    if len(password) < 10:
        return False, t('backend.password.too_short')
    if not any(c.isupper() for c in password):
        return False, t('backend.password.no_uppercase')
    if not any(c.islower() for c in password):
        return False, t('backend.password.no_lowercase')
    if not any(c.isdigit() for c in password):
        return False, t('backend.password.no_digit')
    return True, ""

@app.route('/admin/users', methods=['POST'])
@admin_required
def create_viewer_user():
    """Create a new viewer user."""
    data = request.get_json()
    if not data:
        return jsonify({'error': t('backend.error.no_data')}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': t('backend.error.username_password_required')}), 400

    is_strong, msg = is_password_strong(password)
    if not is_strong:
        return jsonify({'error': msg}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': t('backend.error.user_exists').format(name=username)}), 409

    user = User(username=username, role='viewer')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({'status': 'ok', 'message': t('backend.success.user_created').format(name=username), 'user': {'id': user.id, 'username': user.username, 'role': user.role}}), 201

@app.route('/admin/users/<int:user_id>', methods=['PUT'])
@admin_required
def update_viewer_user(user_id):
    """Update a viewer user's username."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': t('backend.error.user_not_found')}), 404
    if user.is_admin:
        return jsonify({'error': t('backend.error.cannot_modify_admin')}), 403

    data = request.get_json()
    new_username = data.get('username', '').strip() if data else ''
    if not new_username:
        return jsonify({'error': t('backend.error.username_required')}), 400

    existing = User.query.filter_by(username=new_username).first()
    if existing and existing.id != user_id:
        return jsonify({'error': t('backend.error.username_taken').format(name=new_username)}), 409

    user.username = new_username
    db.session.commit()
    return jsonify({'status': 'ok', 'message': t('backend.success.username_updated').format(name=new_username)})

@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def reset_viewer_password(user_id):
    """Reset a viewer user's password."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': t('backend.error.user_not_found')}), 404
    if user.is_admin:
        return jsonify({'error': t('backend.error.cannot_reset_admin_password')}), 403

    data = request.get_json()
    new_password = data.get('password', '') if data else ''

    is_strong, msg = is_password_strong(new_password)
    if not is_strong:
        return jsonify({'error': msg}), 400

    user.set_password(new_password)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': t('backend.success.password_reset').format(name=user.username)})

@app.route('/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_viewer_user(user_id):
    """Delete a viewer user."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': t('backend.error.user_not_found')}), 404
    if user.is_admin:
        return jsonify({'error': t('backend.error.cannot_delete_admin')}), 403

    username = user.username
    db.session.delete(user)
    db.session.commit()
    return jsonify({'status': 'ok', 'message': t('backend.success.user_deleted').format(name=username)})


# ------------------ Admin: camera groups CRUD ------------------

@app.route('/admin/camera-groups', methods=['GET'])
@admin_required
def admin_list_camera_groups():
    return _proxy_to_db_manager('GET', '/api/admin/camera-groups')


@app.route('/admin/camera-groups', methods=['POST'])
@admin_required
def admin_create_camera_group():
    return _proxy_to_db_manager('POST', '/api/admin/camera-groups', request.get_json(silent=True))


@app.route('/admin/camera-groups/<int:group_id>', methods=['GET'])
@admin_required
def admin_get_camera_group(group_id):
    return _proxy_to_db_manager('GET', f'/api/admin/camera-groups/{group_id}')


@app.route('/admin/camera-groups/<int:group_id>', methods=['PUT'])
@admin_required
def admin_update_camera_group(group_id):
    return _proxy_to_db_manager('PUT', f'/api/admin/camera-groups/{group_id}', request.get_json(silent=True))


@app.route('/admin/camera-groups/<int:group_id>', methods=['DELETE'])
@admin_required
def admin_delete_camera_group(group_id):
    return _proxy_to_db_manager('DELETE', f'/api/admin/camera-groups/{group_id}')


@app.route('/admin/camera-groups/<int:group_id>/cameras', methods=['GET'])
@admin_required
def admin_list_group_cameras(group_id):
    return _proxy_to_db_manager('GET', f'/api/admin/camera-groups/{group_id}/cameras')


@app.route('/admin/camera-groups/<int:group_id>/cameras', methods=['PUT'])
@admin_required
def admin_set_group_cameras(group_id):
    return _proxy_to_db_manager('PUT', f'/api/admin/camera-groups/{group_id}/cameras', request.get_json(silent=True))


@app.route('/admin/users/<int:user_id>/camera-groups', methods=['GET'])
@admin_required
def admin_list_user_camera_groups(user_id):
    return _proxy_to_db_manager('GET', f'/api/admin/users/{user_id}/camera-groups')


@app.route('/admin/users/<int:user_id>/camera-groups', methods=['PUT'])
@admin_required
def admin_set_user_camera_groups(user_id):
    return _proxy_to_db_manager('PUT', f'/api/admin/users/{user_id}/camera-groups', request.get_json(silent=True))


def _proxy_to_db_manager(method, path_suffix, json_body=None):
    """Internal helper: forward request to db-manager and return its response.

    Used by /admin/camera-groups/* endpoints which have @admin_required but
    need to hit the corresponding /api/admin/... endpoint on the db-manager.
    """
    url = f"{DB_MANAGER_API_URL}{path_suffix}"
    try:
        if method == 'GET':
            r = requests.get(url, timeout=10)
        elif method == 'POST':
            r = requests.post(url, json=json_body, timeout=10)
        elif method == 'PUT':
            r = requests.put(url, json=json_body, timeout=10)
        elif method == 'DELETE':
            r = requests.delete(url, timeout=10)
        else:
            return jsonify({"error": t('backend.error.unsupported_method').format(method=method)}), 405
        return r.content, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')}
    except requests.exceptions.RequestException as e:
        return jsonify({"error": t('backend.error.db_manager_unreachable').format(error=str(e))}), 503


# --- API Proxy Routes ---

@app.route('/api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def api_proxy(path):
    """Proxy API requests to the DB Manager service.
    Viewer users are restricted to GET requests only.
    """
    # Role-based restriction: viewers can only read
    if not current_user.is_admin and request.method != 'GET':
        return jsonify({"error": t('backend.error.permission_denied_readonly')}), 403

    url = f"{DB_MANAGER_API_URL}/api/{path}"

    # Build query string: forward client params + inject allowed_camera_ids for viewers.
    from urllib.parse import urlencode
    forwarded_params = []
    if request.query_string:
        forwarded_params.append(request.query_string.decode('utf-8'))
    if current_user.is_authenticated and not current_user.is_admin:
        allowed = get_allowed_camera_ids(current_user)
        # allowed is [] (empty list) for viewers without groups; send empty param so db-manager
        # treats it as "deny all" rather than "no filter".
        forwarded_params.append(urlencode({"allowed_camera_ids": ",".join(str(i) for i in allowed)}))
    if forwarded_params:
        url += "?" + "&".join(forwarded_params)

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
        return jsonify({"error": t('backend.error.db_connect').format(error=str(e))}), 503

def _send_image_or_jxl_fallback(images_dir, filename):
    """Serve filename from images_dir, decoding from sibling .jxl if the .jpg is absent.

    Returns the Flask response, or aborts 404 if neither file is available, the
    requested path escapes images_dir, or djxl fails.
    """
    jpg_path = safe_join(images_dir, filename)
    if jpg_path is None:
        abort(404)
    if os.path.exists(jpg_path):
        return send_from_directory(images_dir, filename)

    if not filename.endswith('.jpg'):
        abort(404)
    jxl_path = jpg_path[:-4] + '.jxl'
    if not os.path.exists(jxl_path):
        abort(404)

    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tf:
        tmp_path = tf.name
    try:
        result = subprocess.run(
            ['djxl', jxl_path, tmp_path],
            capture_output=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            current_app.logger.warning(
                "djxl rc=%d on %s: %s",
                result.returncode, jxl_path, result.stderr[:200].decode('utf-8', errors='replace'),
            )
            abort(404)
        with open(tmp_path, 'rb') as f:
            data = f.read()
        return Response(data, mimetype='image/jpeg')
    except subprocess.TimeoutExpired:
        current_app.logger.warning("djxl timeout on %s", jxl_path)
        abort(404)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route('/images/<path:filename>')
@login_required
def serve_image(filename):
    """Serve images from the anpr_images directory, gated by group access for viewers."""
    images_dir = '/app/anpr_images'

    # Admin: serve unconditionally
    if current_user.is_admin:
        return _send_image_or_jxl_fallback(images_dir, filename)

    # Viewer: look up the event's camera_id and check it's in allowed list
    allowed = get_allowed_camera_ids(current_user)
    if not allowed:
        abort(403)

    row = db.session.execute(
        db.text("SELECT camera_id FROM anpr_events WHERE image_filename = :fn LIMIT 1"),
        {"fn": filename}
    ).fetchone()
    if row is None:
        abort(404)
    camera_id = row[0]
    if camera_id is None or camera_id not in allowed:
        abort(403)

    return _send_image_or_jxl_fallback(images_dir, filename)

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

    # Add 'ip_address' column to sessions table if it doesn't exist
    try:
        session_model = app.config.get('SESSION_SQLALCHEMY_TABLE', 'sessions')
        db.session.execute(db.text(
            f"ALTER TABLE {session_model} ADD COLUMN ip_address VARCHAR(45) NULL"
        ))
        db.session.commit()
        print(f"Added 'ip_address' column to {session_model} table.")
    except Exception:
        db.session.rollback()
        # Column already exists, no action needed




if __name__ == '__main__':
    server_port = int(os.getenv('FLASK_RUN_PORT', 5000))
    app.run(host='0.0.0.0', port=server_port, debug=False)
