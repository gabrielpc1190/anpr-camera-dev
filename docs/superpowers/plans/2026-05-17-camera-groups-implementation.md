# Camera Groups + Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar grupos de cámaras con control de acceso por grupo, restringiendo a los viewers a ver solo cámaras de sus grupos asignados. Admins ven todo (sin cambio).

**Architecture:** 3 tablas nuevas en MariaDB (camera_groups, camera_group_members M:N, user_camera_groups M:N). El servicio `anpr-web` computa `allowed_camera_ids` por viewer y lo pasa como query param al `anpr-db-manager`, que filtra al nivel SQL. Admin UI completa en `admin.html` (tab Grupos + extensión del modal de Users).

**Tech Stack:** Python 3.11, Flask, Flask-Login, mysql-connector-python (db-manager), SQLAlchemy (anpr-web), MariaDB 10.6, Docker Compose, vanilla JS + Tailwind (frontend).

**Spec de referencia:** [docs/superpowers/specs/2026-05-17-camera-groups-and-access-design.md](../specs/2026-05-17-camera-groups-and-access-design.md)
**Runbook operacional:** [docs/deployment/camera-groups-migration.md](../../deployment/camera-groups-migration.md)

---

## Estado actual del repositorio

- En `master`, commit `efe5526`. Working tree limpio salvo `?? input.txt` (no relacionado).
- Producción corriendo con código pre-groups. Tablas existentes: `anpr_events`, `cameras`, `user`, `sessions`. 4 viewers + 2 admins. 4 cámaras.

## Setup compartido (lee esto antes de empezar)

**Permisos**: el código vive en `/root/anpr-camera-dev/` que es propiedad de root. Todos los comandos de lectura/escritura van con `sudo`. Los archivos se editan en `/tmp/` (propiedad del usuario `gabriel`) y se copian con `sudo cp`.

**Patrón de edición + deploy**:
```bash
sudo cp /root/anpr-camera-dev/app/<file> /tmp/<file>
sudo chown gabriel:gabriel /tmp/<file>
# Editar /tmp/<file> con Edit tool
python3 -m py_compile /tmp/<file>    # solo Python
sudo cp /tmp/<file> /root/anpr-camera-dev/app/<file>
sudo chown root:root /root/anpr-camera-dev/app/<file>
cd /root/anpr-camera-dev && sudo docker-compose build <service>
cd /root/anpr-camera-dev && sudo docker-compose up -d <service>
```

**Important**: `docker restart` no aplica cambios al código Python (está baked en la imagen). Siempre usar `build` + `up -d`.

**Variables de DB**:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
ROOT_PASS=$(sudo grep "^MYSQL_ROOT_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
```

**Push del SSH (cuando llegue el commit final)**:
```bash
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" \
  git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master
```

---

## File structure

| Archivo | Cambio | Responsabilidad |
|---|---|---|
| `app/anpr_db_manager.py` | Modify (substantial) | DDL de 3 nuevas tablas en `initialize_database()`. Filter helper. Filter en 3 endpoints existentes. 9 nuevos endpoints admin para CRUD de grupos y memberships. |
| `app/anpr_web.py` | Modify (substantial) | Helper `get_allowed_camera_ids`. Inyección en `api_proxy`. Validación en `serve_image`. Mirror de 9 endpoints admin con `@admin_required` y proxy al db-manager. |
| `app/templates/admin.html` | Modify (substantial) | Tab "Grupos" nuevo. Modales de Crear/Editar grupo. Extensión del modal de viewers existente con multi-select de grupos. |
| `app/templates/index.html` | No change | Funciona transparente (lee `/api/cameras` y `/api/events` ya filtrados). |
| `backups/anpr_events_pre_groups_<TS>.sql.gz` | Create | Dump pre-deploy. |
| `backups/code_pre_groups_<TS>.tar.gz` | Create | Tar de archivos pre-deploy. |

No se crean archivos Python nuevos. La complejidad se acomoda dentro de los archivos existentes siguiendo el patrón monolítico actual.

---

## Task 1: Pre-flight check + backups

**Files:** ninguno (solo lectura/verificación) + 2 backups creados

- [ ] **Step 1.1: Verificar estado del repo + producción**

Run:
```bash
sudo git -C /root/anpr-camera-dev status --short --branch
sudo docker ps --filter name=anpr --format "table {{.Names}}\t{{.Status}}"
curl -s -o /dev/null -w "anpr-db-manager: HTTP %{http_code}\n" http://localhost:5001/health
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected:
- branch `master` aligned with `origin/master` (no ahead/behind)
- todos los containers `Up` (db-manager y mariadb `healthy`)
- ambos health endpoints HTTP 200

Si falla algo, parar y reportar antes de tocar nada.

- [ ] **Step 1.2: Capturar baseline de tablas (para verificar después que las nuevas se crearon)**

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -Nse \
  "SHOW TABLES;" 2>/dev/null
```

Expected: exactamente 4 tablas listadas: `anpr_events`, `cameras`, `sessions`, `user`. Las nuevas (`camera_groups`, etc.) NO deben existir aún.

- [ ] **Step 1.3: Crear respaldo de DB (dump comprimido)**

Run:
```bash
TS=$(date +%Y%m%d_%H%M%S)
echo "TS=$TS"
BACKUP_DIR=/root/anpr-camera-dev/backups
ROOT_PASS=$(sudo grep "^MYSQL_ROOT_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)

sudo docker exec anpr-mariadb mysqldump \
  -u root -p"$ROOT_PASS" \
  --single-transaction --routines --triggers --events \
  --default-character-set=utf8mb4 \
  anpr_events 2>/dev/null | sudo tee "$BACKUP_DIR/anpr_events_pre_groups_${TS}.sql" > /dev/null
sudo gzip "$BACKUP_DIR/anpr_events_pre_groups_${TS}.sql"
sudo ls -lah "$BACKUP_DIR/anpr_events_pre_groups_${TS}.sql.gz"
```

Expected: archivo creado, tamaño 5-10 MB. **Anotar el valor de `TS`** — se reusa en Step 1.4 y en rollback eventual.

- [ ] **Step 1.4: Crear respaldo de código (tar de archivos que vamos a tocar)**

Run (usar el mismo `TS` del paso anterior):
```bash
sudo tar czf "/root/anpr-camera-dev/backups/code_pre_groups_${TS}.tar.gz" \
  -C /root/anpr-camera-dev \
  app/anpr_db_manager.py \
  app/anpr_web.py \
  app/models.py \
  app/templates/admin.html \
  app/templates/index.html
sudo ls -lah "/root/anpr-camera-dev/backups/code_pre_groups_${TS}.tar.gz"
sudo tar tzf "/root/anpr-camera-dev/backups/code_pre_groups_${TS}.tar.gz"
```

Expected: archivo creado ~25-40 KB. Listado muestra los 5 archivos tarred correctamente.

---

## Task 2: Schema DDL — agregar 3 tablas en `initialize_database()`

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_db_manager.py` (función `initialize_database()` alrededor de línea 78-150)

- [ ] **Step 2.1: Sacar el archivo a /tmp para editar**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_db_manager.py /tmp/anpr_db_manager.py
sudo chown gabriel:gabriel /tmp/anpr_db_manager.py
```

- [ ] **Step 2.2: Localizar el final del bloque actual de `initialize_database()` y agregar las 3 nuevas tablas**

Buscar en `/tmp/anpr_db_manager.py` la última línea DDL dentro de `initialize_database()` (justo antes de `conn.commit()`). Insertar inmediatamente antes del `conn.commit()` el siguiente bloque:

```python
        # --- Camera groups + access control (M:N relations) ---
        logger.info("Ensuring 'camera_groups' table exists...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camera_groups (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        logger.info("Ensuring 'camera_group_members' table exists...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camera_group_members (
                camera_id INT NOT NULL,
                group_id INT NOT NULL,
                PRIMARY KEY (camera_id, group_id),
                FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES camera_groups(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        logger.info("Ensuring 'user_camera_groups' table exists...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_camera_groups (
                user_id INT NOT NULL,
                group_id INT NOT NULL,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
                FOREIGN KEY (group_id) REFERENCES camera_groups(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
```

NO modificar nada más. NO agregar data seeding.

- [ ] **Step 2.3: Validar sintaxis Python**

Run:
```bash
python3 -m py_compile /tmp/anpr_db_manager.py && echo "Sintaxis OK"
```

Expected: `Sintaxis OK`. Si falla, revisar la indentación del bloque insertado (debe estar al mismo nivel que los demás CREATE TABLE dentro de la función).

- [ ] **Step 2.4: Deploy + verificar tablas creadas**

Run:
```bash
sudo cp /tmp/anpr_db_manager.py /root/anpr-camera-dev/app/anpr_db_manager.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_db_manager.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-db-manager
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-db-manager
sleep 20
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:5001/health
```

Expected: build OK, restart OK, `HTTP 200`. Si timeout o 5xx, revisar logs:
```bash
sudo docker logs --since=1m anpr-db-manager 2>&1 | tail -30
```

- [ ] **Step 2.5: Verificar que las 3 tablas existen y tienen el schema correcto**

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SHOW TABLES;" 2>&1 | grep -v "Using a password"
echo ""
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "DESCRIBE camera_groups; DESCRIBE camera_group_members; DESCRIBE user_camera_groups;" 2>&1 | grep -v "Using a password"
```

Expected: las 3 nuevas tablas aparecen en `SHOW TABLES`. `DESCRIBE` muestra:
- `camera_groups`: id (INT PK AI), name (VARCHAR 255 UK NOT NULL), description (TEXT NULL), created_at, updated_at
- `camera_group_members`: camera_id (INT, PRI), group_id (INT, PRI)
- `user_camera_groups`: user_id (INT, PRI), group_id (INT, PRI)

---

## Task 3: db-manager — Helper `_parse_allowed_camera_ids` + filtro en 3 endpoints

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_db_manager.py` (agregar helper antes de `get_events`, modificar `get_events`, `get_cameras`, `get_latest_timestamp`)

- [ ] **Step 3.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_db_manager.py /tmp/anpr_db_manager.py
sudo chown gabriel:gabriel /tmp/anpr_db_manager.py
```

- [ ] **Step 3.2: Agregar helper `_parse_allowed_camera_ids` antes de la primera ruta `/api/...`**

Buscar la línea `@app.route('/api/events', methods=['GET'])` (alrededor de línea 354). Insertar INMEDIATAMENTE ANTES de esa línea:

```python
def _parse_allowed_camera_ids():
    """Parse 'allowed_camera_ids' query param into a filter mode.

    Returns:
        None  -> param absent (admin path, no filtering)
        []    -> param present but empty (viewer without groups, deny all)
        [int, ...] -> param present with values (filter to these ids)
    """
    if 'allowed_camera_ids' not in request.args:
        return None
    raw = request.args.get('allowed_camera_ids', '').strip()
    if not raw:
        return []
    out = []
    for token in raw.split(','):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            logger.warning(f"Ignoring invalid camera id in allowed_camera_ids: '{token}'")
    return out
```

- [ ] **Step 3.3: Aplicar el filtro en `get_events()`**

Buscar dentro de `get_events()` (alrededor de línea 355) la sección donde se construye `where_clauses`. Justo después de declarar `query_params, where_clauses = [], []`, agregar:

```python
    allowed = _parse_allowed_camera_ids()
    if allowed is not None:
        if not allowed:
            # Viewer sin acceso a ninguna cámara: respuesta vacía rápida.
            return jsonify({
                "events": [], "total_pages": 0,
                "current_page": page, "total_events": 0
            })
        placeholders = ','.join(['%s'] * len(allowed))
        where_clauses.append(f"camera_id IN ({placeholders})")
        query_params.extend(allowed)
```

(`page` ya está definida más arriba en la función. `placeholders` se construye dinámicamente porque `IN (...)` no acepta una sola variable.)

- [ ] **Step 3.4: Aplicar el filtro en `get_cameras()`**

Buscar `def get_cameras():` (alrededor de línea 474). El cuerpo actual hace `SELECT ... FROM cameras WHERE enabled = TRUE`. Modificarlo para construir el WHERE dinámicamente:

Reemplazar el contenido de `get_cameras()` entero por:

```python
@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        allowed = _parse_allowed_camera_ids()
        if allowed is not None and not allowed:
            return jsonify({"cameras": []})

        where = ["enabled = TRUE"]
        params = []
        if allowed:
            placeholders = ','.join(['%s'] * len(allowed))
            where.append(f"id IN ({placeholders})")
            params.extend(allowed)

        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT id, friendly_name, ip_address, port "
            f"FROM cameras WHERE {' AND '.join(where)} ORDER BY friendly_name",
            params
        )
        cameras = cursor.fetchall()
        return jsonify({"cameras": cameras})
    except mysql.connector.Error as err:
        logger.error(f"Error fetching cameras: {err}", exc_info=True)
        return jsonify({"cameras": []}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
```

- [ ] **Step 3.5: Aplicar el filtro en `get_latest_timestamp()`**

Buscar `def get_latest_timestamp():` (alrededor de línea 496). Localizar la query `SELECT MAX(timestamp) FROM anpr_events` y la query `SELECT COUNT(*) FROM anpr_events WHERE timestamp > %s`. Agregar el filtro de allowed_camera_ids a AMBAS.

Reemplazar el bloque interno del try/except así. El try actual ya construye `cursor` y ejecuta queries — modifícalo siguiendo este patrón:

```python
    cursor = None
    try:
        allowed = _parse_allowed_camera_ids()
        if allowed is not None and not allowed:
            return jsonify({"latest_timestamp": None, "new_events_count": 0})

        extra_where = ""
        extra_params = []
        if allowed:
            placeholders = ','.join(['%s'] * len(allowed))
            extra_where = f" WHERE camera_id IN ({placeholders})"
            extra_params = list(allowed)

        cursor = conn.cursor()
        cursor.execute(f"SELECT MAX(timestamp) FROM anpr_events{extra_where}", extra_params)
        latest_timestamp = cursor.fetchone()[0]

        new_events_count = 0
        if since_timestamp_str and latest_timestamp:
            try:
                since_timestamp_obj = datetime.fromisoformat(since_timestamp_str.replace('Z', '+00:00'))
                where_clause = "timestamp > %s"
                params = [since_timestamp_obj]
                if allowed:
                    placeholders = ','.join(['%s'] * len(allowed))
                    where_clause += f" AND camera_id IN ({placeholders})"
                    params.extend(allowed)
                cursor.execute(f"SELECT COUNT(*) FROM anpr_events WHERE {where_clause}", params)
                new_events_count = cursor.fetchone()[0]
            except (ValueError, TypeError):
                logger.warning(f"Invalid 'since' timestamp format received: {since_timestamp_str}")

        return jsonify({
            "latest_timestamp": latest_timestamp.isoformat() if latest_timestamp else None,
            "new_events_count": new_events_count
        })
```

(El resto de la función — el `if not conn`, el `since_timestamp_str = request.args.get(...)`, el `except mysql.connector.Error`, el `finally` — se queda igual.)

- [ ] **Step 3.6: Validar sintaxis + deploy + smoke test**

Run:
```bash
python3 -m py_compile /tmp/anpr_db_manager.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_db_manager.py /root/anpr-camera-dev/app/anpr_db_manager.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_db_manager.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-db-manager
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-db-manager
sleep 20
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:5001/health
```

Expected: `Sintaxis OK`, build, `HTTP 200`.

- [ ] **Step 3.7: Verificar 4 modos del filtro vía curl directo al db-manager**

Run:
```bash
echo "=== 1. SIN param (admin path) — debe devolver todas ==="
curl -s "http://localhost:5001/api/cameras" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d[\"cameras\"])} cameras')"

echo "=== 2. Param vacío (viewer sin grupos) — debe devolver 0 ==="
curl -s "http://localhost:5001/api/cameras?allowed_camera_ids=" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d[\"cameras\"])} cameras')"

echo "=== 3. Param con 1 id — debe devolver solo esa ==="
curl -s "http://localhost:5001/api/cameras?allowed_camera_ids=1" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'cameras: {[c[\"id\"] for c in d[\"cameras\"]]}')"

echo "=== 4. Param con varios ids — debe devolver el subconjunto ==="
curl -s "http://localhost:5001/api/cameras?allowed_camera_ids=1,3,4" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'cameras: {[c[\"id\"] for c in d[\"cameras\"]]}')"

echo "=== 5. Mismo test sobre /api/events ==="
curl -s "http://localhost:5001/api/events?limit=2&allowed_camera_ids=3" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'total: {d[\"total_events\"]}, sample camera_ids: {[e[\"camera_id\"] for e in d[\"events\"]]}')"

echo "=== 6. /api/events/latest_timestamp con filtro ==="
curl -s "http://localhost:5001/api/events/latest_timestamp?allowed_camera_ids=3" | python3 -m json.tool
```

Expected:
- Test 1: 4 cameras
- Test 2: 0 cameras
- Test 3: solo [1]
- Test 4: [1, 3, 4]
- Test 5: events solo con camera_id=3
- Test 6: latest_timestamp basado solo en eventos camera_id=3

Si alguno falla, revisar la query construida agregando `logger.debug(sql_query, params)` temporalmente y mirando logs.

---

## Task 4: db-manager — Endpoints admin para CRUD de `camera_groups`

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_db_manager.py` (agregar nuevos endpoints después de `/api/events/latest_timestamp`)

- [ ] **Step 4.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_db_manager.py /tmp/anpr_db_manager.py
sudo chown gabriel:gabriel /tmp/anpr_db_manager.py
```

- [ ] **Step 4.2: Agregar los 5 endpoints de CRUD de grupos**

Buscar `@app.route('/health', methods=['GET'])` (alrededor de línea 531). Insertar INMEDIATAMENTE ANTES de esa línea el siguiente bloque:

```python
# ------------------ Admin: camera_groups CRUD ------------------

@app.route('/api/admin/camera-groups', methods=['GET'])
def admin_list_camera_groups():
    """List all groups with counts."""
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT cg.id, cg.name, cg.description, cg.created_at, cg.updated_at,
                   (SELECT COUNT(*) FROM camera_group_members WHERE group_id = cg.id) AS camera_count,
                   (SELECT COUNT(*) FROM user_camera_groups WHERE group_id = cg.id) AS user_count
            FROM camera_groups cg
            ORDER BY cg.name
        """)
        groups = cursor.fetchall()
        for g in groups:
            if g.get('created_at'): g['created_at'] = g['created_at'].isoformat()
            if g.get('updated_at'): g['updated_at'] = g['updated_at'].isoformat()
        return jsonify({"groups": groups})
    except mysql.connector.Error as err:
        logger.error(f"Error listing camera groups: {err}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/camera-groups', methods=['POST'])
def admin_create_camera_group():
    """Create a group. Body: {name, description?}."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    description = data.get('description')
    if not name:
        return jsonify({"error": "name is required"}), 400
    if len(name) > 255:
        return jsonify({"error": "name too long (max 255)"}), 400

    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO camera_groups (name, description) VALUES (%s, %s)",
            (name, description)
        )
        new_id = cursor.lastrowid
        conn.commit()
        return jsonify({"id": new_id, "name": name, "description": description}), 201
    except mysql.connector.IntegrityError:
        return jsonify({"error": f"A group named '{name}' already exists"}), 409
    except mysql.connector.Error as err:
        logger.error(f"Error creating camera group: {err}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/camera-groups/<int:group_id>', methods=['GET'])
def admin_get_camera_group(group_id):
    """Detail of a single group."""
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, description, created_at, updated_at FROM camera_groups WHERE id = %s",
            (group_id,)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        if row.get('created_at'): row['created_at'] = row['created_at'].isoformat()
        if row.get('updated_at'): row['updated_at'] = row['updated_at'].isoformat()
        return jsonify(row)
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/camera-groups/<int:group_id>', methods=['PUT'])
def admin_update_camera_group(group_id):
    """Update name and/or description. Body: {name?, description?}."""
    data = request.get_json(silent=True) or {}
    fields = []
    params = []
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({"error": "name cannot be empty"}), 400
        if len(name) > 255:
            return jsonify({"error": "name too long (max 255)"}), 400
        fields.append("name = %s")
        params.append(name)
    if 'description' in data:
        fields.append("description = %s")
        params.append(data.get('description'))
    if not fields:
        return jsonify({"error": "no updatable fields supplied"}), 400

    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor()
        params.append(group_id)
        cursor.execute(f"UPDATE camera_groups SET {', '.join(fields)} WHERE id = %s", params)
        if cursor.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        conn.commit()
        return jsonify({"status": "ok"})
    except mysql.connector.IntegrityError:
        return jsonify({"error": "name conflict"}), 409
    except mysql.connector.Error as err:
        logger.error(f"Error updating camera group {group_id}: {err}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/camera-groups/<int:group_id>', methods=['DELETE'])
def admin_delete_camera_group(group_id):
    """Delete a group (CASCADE removes memberships)."""
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM camera_groups WHERE id = %s", (group_id,))
        if cursor.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        conn.commit()
        return jsonify({"status": "ok"})
    except mysql.connector.Error as err:
        logger.error(f"Error deleting camera group {group_id}: {err}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
```

- [ ] **Step 4.3: Validar sintaxis + deploy**

Run:
```bash
python3 -m py_compile /tmp/anpr_db_manager.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_db_manager.py /root/anpr-camera-dev/app/anpr_db_manager.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_db_manager.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-db-manager
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-db-manager
sleep 20
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:5001/health
```

- [ ] **Step 4.4: Smoke test del CRUD completo**

Run:
```bash
echo "=== Lista inicial (vacía) ==="
curl -s http://localhost:5001/api/admin/camera-groups | python3 -m json.tool

echo "=== POST crear grupo ==="
CREATED=$(curl -s -X POST http://localhost:5001/api/admin/camera-groups \
  -H "Content-Type: application/json" \
  -d '{"name": "TestGroup", "description": "Smoke test"}')
echo "$CREATED" | python3 -m json.tool
GID=$(echo "$CREATED" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "Created id=$GID"

echo "=== GET detail ==="
curl -s "http://localhost:5001/api/admin/camera-groups/$GID" | python3 -m json.tool

echo "=== POST duplicado (debe dar 409) ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST http://localhost:5001/api/admin/camera-groups \
  -H "Content-Type: application/json" -d '{"name": "TestGroup"}'

echo "=== PUT update ==="
curl -s -X PUT "http://localhost:5001/api/admin/camera-groups/$GID" \
  -H "Content-Type: application/json" \
  -d '{"description": "Updated"}' | python3 -m json.tool

echo "=== DELETE ==="
curl -s -X DELETE "http://localhost:5001/api/admin/camera-groups/$GID" | python3 -m json.tool

echo "=== GET 404 ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" "http://localhost:5001/api/admin/camera-groups/$GID"
```

Expected: 1) lista vacía, 2) crea OK con id, 3) detail OK, 4) HTTP 409 en duplicado, 5) update OK, 6) delete OK, 7) HTTP 404 post-delete.

---

## Task 5: db-manager — Endpoints admin para membership (camera↔group, user↔group)

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_db_manager.py` (agregar después de los endpoints de Task 4)

- [ ] **Step 5.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_db_manager.py /tmp/anpr_db_manager.py
sudo chown gabriel:gabriel /tmp/anpr_db_manager.py
```

- [ ] **Step 5.2: Agregar los 4 endpoints de membership**

Buscar `@app.route('/health', methods=['GET'])`. Insertar INMEDIATAMENTE ANTES:

```python
# ------------------ Admin: camera <-> group membership ------------------

@app.route('/api/admin/camera-groups/<int:group_id>/cameras', methods=['GET'])
def admin_list_group_cameras(group_id):
    """List cameras in a specific group."""
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT 1 FROM camera_groups WHERE id = %s", (group_id,))
        if not cursor.fetchone():
            return jsonify({"error": "group not found"}), 404
        cursor.execute("""
            SELECT c.id, c.friendly_name, c.ip_address, c.port
            FROM cameras c
            JOIN camera_group_members cgm ON cgm.camera_id = c.id
            WHERE cgm.group_id = %s
            ORDER BY c.friendly_name
        """, (group_id,))
        return jsonify({"cameras": cursor.fetchall()})
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/camera-groups/<int:group_id>/cameras', methods=['PUT'])
def admin_set_group_cameras(group_id):
    """Replace membership of cameras in this group. Body: {camera_ids: [int, ...]}."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('camera_ids')
    if not isinstance(raw_ids, list):
        return jsonify({"error": "camera_ids must be a list"}), 400
    camera_ids = []
    for c in raw_ids:
        try:
            camera_ids.append(int(c))
        except (ValueError, TypeError):
            return jsonify({"error": f"invalid camera id: {c}"}), 400

    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM camera_groups WHERE id = %s", (group_id,))
        if not cursor.fetchone():
            return jsonify({"error": "group not found"}), 404

        # Validate all camera_ids exist in cameras table
        if camera_ids:
            placeholders = ','.join(['%s'] * len(camera_ids))
            cursor.execute(f"SELECT id FROM cameras WHERE id IN ({placeholders})", camera_ids)
            found = {row[0] for row in cursor.fetchall()}
            missing = [cid for cid in camera_ids if cid not in found]
            if missing:
                return jsonify({"error": f"camera ids not found: {missing}"}), 400

        cursor.execute("DELETE FROM camera_group_members WHERE group_id = %s", (group_id,))
        if camera_ids:
            cursor.executemany(
                "INSERT INTO camera_group_members (camera_id, group_id) VALUES (%s, %s)",
                [(cid, group_id) for cid in camera_ids]
            )
        conn.commit()
        return jsonify({"status": "ok", "count": len(camera_ids)})
    except mysql.connector.Error as err:
        conn.rollback()
        logger.error(f"Error setting group {group_id} cameras: {err}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/users/<int:user_id>/camera-groups', methods=['GET'])
def admin_list_user_camera_groups(user_id):
    """List groups assigned to a user."""
    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT cg.id, cg.name, cg.description
            FROM camera_groups cg
            JOIN user_camera_groups ucg ON ucg.group_id = cg.id
            WHERE ucg.user_id = %s
            ORDER BY cg.name
        """, (user_id,))
        return jsonify({"groups": cursor.fetchall()})
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()


@app.route('/api/admin/users/<int:user_id>/camera-groups', methods=['PUT'])
def admin_set_user_camera_groups(user_id):
    """Replace group assignments for a user. Body: {group_ids: [int, ...]}."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('group_ids')
    if not isinstance(raw_ids, list):
        return jsonify({"error": "group_ids must be a list"}), 400
    group_ids = []
    for g in raw_ids:
        try:
            group_ids.append(int(g))
        except (ValueError, TypeError):
            return jsonify({"error": f"invalid group id: {g}"}), 400

    conn = get_db_connection()
    if not conn: abort(503, description="Database connection unavailable")
    cursor = None
    try:
        cursor = conn.cursor()

        # Validate all group_ids exist
        if group_ids:
            placeholders = ','.join(['%s'] * len(group_ids))
            cursor.execute(f"SELECT id FROM camera_groups WHERE id IN ({placeholders})", group_ids)
            found = {row[0] for row in cursor.fetchall()}
            missing = [gid for gid in group_ids if gid not in found]
            if missing:
                return jsonify({"error": f"group ids not found: {missing}"}), 400

        cursor.execute("DELETE FROM user_camera_groups WHERE user_id = %s", (user_id,))
        if group_ids:
            cursor.executemany(
                "INSERT INTO user_camera_groups (user_id, group_id) VALUES (%s, %s)",
                [(user_id, gid) for gid in group_ids]
            )
        conn.commit()
        return jsonify({"status": "ok", "count": len(group_ids)})
    except mysql.connector.Error as err:
        conn.rollback()
        logger.error(f"Error setting user {user_id} groups: {err}", exc_info=True)
        return jsonify({"error": "Database error"}), 500
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
```

- [ ] **Step 5.3: Validar sintaxis + deploy + smoke test integral**

Run:
```bash
python3 -m py_compile /tmp/anpr_db_manager.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_db_manager.py /root/anpr-camera-dev/app/anpr_db_manager.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_db_manager.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-db-manager
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-db-manager
sleep 20
```

Smoke test (memberships):
```bash
echo "=== Crear grupo de prueba ==="
GID=$(curl -s -X POST http://localhost:5001/api/admin/camera-groups \
  -H "Content-Type: application/json" \
  -d '{"name": "SmokeMember"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "GID=$GID"

echo "=== PUT assignment cameras 1,3 ==="
curl -s -X PUT "http://localhost:5001/api/admin/camera-groups/$GID/cameras" \
  -H "Content-Type: application/json" -d '{"camera_ids": [1, 3]}' | python3 -m json.tool

echo "=== GET cameras del grupo ==="
curl -s "http://localhost:5001/api/admin/camera-groups/$GID/cameras" | python3 -m json.tool

echo "=== PUT reemplaza con solo [4] ==="
curl -s -X PUT "http://localhost:5001/api/admin/camera-groups/$GID/cameras" \
  -H "Content-Type: application/json" -d '{"camera_ids": [4]}' | python3 -m json.tool

echo "=== GET confirma reemplazo ==="
curl -s "http://localhost:5001/api/admin/camera-groups/$GID/cameras" | python3 -m json.tool

echo "=== PUT camera id inexistente (debe dar 400) ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X PUT "http://localhost:5001/api/admin/camera-groups/$GID/cameras" \
  -H "Content-Type: application/json" -d '{"camera_ids": [9999]}'

echo "=== Assignar usuario admin (id=1) al grupo ==="
curl -s -X PUT "http://localhost:5001/api/admin/users/1/camera-groups" \
  -H "Content-Type: application/json" -d "{\"group_ids\": [$GID]}" | python3 -m json.tool

echo "=== GET groups del usuario 1 ==="
curl -s "http://localhost:5001/api/admin/users/1/camera-groups" | python3 -m json.tool

echo "=== DELETE grupo (CASCADE limpia members y user assignment) ==="
curl -s -X DELETE "http://localhost:5001/api/admin/camera-groups/$GID" | python3 -m json.tool

echo "=== GET groups del usuario 1 (debe ser vacío post-CASCADE) ==="
curl -s "http://localhost:5001/api/admin/users/1/camera-groups" | python3 -m json.tool
```

Expected: cada operación retorna OK; el PUT con camera_id inexistente da 400; CASCADE limpia correctamente al borrar el grupo.

---

## Task 6: anpr-web — Helper `get_allowed_camera_ids` + inyección en `api_proxy`

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_web.py` (agregar helper + modificar `api_proxy` función)

- [ ] **Step 6.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_web.py /tmp/anpr_web.py
sudo chown gabriel:gabriel /tmp/anpr_web.py
```

- [ ] **Step 6.2: Agregar el helper `get_allowed_camera_ids`**

Buscar en `/tmp/anpr_web.py` el decorador `def admin_required(f):` (alrededor de línea 49). Insertar INMEDIATAMENTE ANTES de ese decorador:

```python
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
```

- [ ] **Step 6.3: Modificar `api_proxy` para inyectar el filtro**

Buscar `def api_proxy(path):` (alrededor de línea 332). El cuerpo actual tiene un bloque que arma la URL y otro que hace los `requests.get/post/...`. Modificar la sección donde se arma la URL — encontrar:

```python
    url = f"{DB_MANAGER_API_URL}/api/{path}"

    # Forward query parameters
    if request.query_string:
        url += f"?{request.query_string.decode('utf-8')}"
```

Reemplazar por:

```python
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
```

(`from urllib.parse import urlencode` se importa dentro de la función para no agregar import top-level que afecte otras partes del archivo.)

- [ ] **Step 6.4: Validar sintaxis + deploy**

Run:
```bash
python3 -m py_compile /tmp/anpr_web.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_web.py /root/anpr-camera-dev/app/anpr_web.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_web.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: `HTTP 200`.

- [ ] **Step 6.5: Smoke test del proxy con login real**

Necesitas un viewer existente para probar. Una cuenta de prueba (no productiva) sería ideal — si no, usa una existente sabiendo que la vas a manipular y restaurar.

Run (usar credenciales del admin para validar el path admin = sin filtro):
```bash
# Login como admin, guardar cookies
ADMIN_USER=admin
read -s -p "Admin password: " ADMIN_PASS && echo
COOKIE=/tmp/cookies_admin.txt
curl -s -c "$COOKIE" -X POST http://localhost:5000/login \
  -d "username=$ADMIN_USER" -d "password=$ADMIN_PASS" -o /dev/null

echo "=== Admin path: /api/cameras debe regresar todas (sin allowed_camera_ids inyectado) ==="
curl -s -b "$COOKIE" "http://localhost:5000/api/cameras" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d[\"cameras\"])} cameras')"
```

Expected: 4 cameras (el path admin no inyecta filtro).

Para el path viewer, hay que probar con uno asignado a grupos (lo haremos en Task 11/E2E). Por ahora confirmar que el path admin sigue funcionando.

---

## Task 7: anpr-web — Validación en `serve_image`

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_web.py` (función `serve_image`)

- [ ] **Step 7.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_web.py /tmp/anpr_web.py
sudo chown gabriel:gabriel /tmp/anpr_web.py
```

- [ ] **Step 7.2: Reemplazar el cuerpo de `serve_image` con validación**

Buscar `def serve_image(filename):` (alrededor de línea 365). El cuerpo actual es muy corto (un `send_from_directory`). Reemplazar la función completa (mantener `@app.route` y `@login_required`):

```python
@app.route('/images/<path:filename>')
@login_required
def serve_image(filename):
    """Serve images from the anpr_images directory, gated by group access for viewers."""
    images_dir = '/app/anpr_images'

    # Admin: serve unconditionally
    if current_user.is_admin:
        return send_from_directory(images_dir, filename)

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

    return send_from_directory(images_dir, filename)
```

- [ ] **Step 7.3: Validar sintaxis + deploy**

Run:
```bash
python3 -m py_compile /tmp/anpr_web.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_web.py /root/anpr-camera-dev/app/anpr_web.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_web.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: `HTTP 200`. Smoke test real con cookies de viewer/admin se hará en Task 11.

---

## Task 8: anpr-web — Mirror de los 9 endpoints admin

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_web.py` (agregar 9 rutas nuevas)

Estos endpoints son thin proxies: validan `@admin_required` y reenvían al db-manager. NO duplican lógica de DB.

- [ ] **Step 8.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_web.py /tmp/anpr_web.py
sudo chown gabriel:gabriel /tmp/anpr_web.py
```

- [ ] **Step 8.2: Agregar helper interno `_proxy_to_db_manager`**

Buscar en el archivo `def api_proxy(path):`. Insertar INMEDIATAMENTE ANTES (en el mismo nivel de indentación de definición de función):

```python
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
            return jsonify({"error": f"unsupported method {method}"}), 405
        return r.content, r.status_code, {'Content-Type': r.headers.get('Content-Type', 'application/json')}
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"db-manager unreachable: {e}"}), 503
```

- [ ] **Step 8.3: Agregar los 5 endpoints de CRUD de grupos**

Después del último `@app.route('/admin/users/<int:user_id>', methods=['DELETE'])` (alrededor de línea 313-329, busca `def delete_viewer_user(user_id):` y termina la función). Insertar:

```python
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
```

- [ ] **Step 8.4: Validar sintaxis + deploy**

Run:
```bash
python3 -m py_compile /tmp/anpr_web.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_web.py /root/anpr-camera-dev/app/anpr_web.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_web.py
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

- [ ] **Step 8.5: Smoke test de admin endpoints vía anpr-web (con cookie admin)**

Run:
```bash
ADMIN_USER=admin
read -s -p "Admin password: " ADMIN_PASS && echo
COOKIE=/tmp/cookies_admin.txt
curl -s -c "$COOKIE" -X POST http://localhost:5000/login -d "username=$ADMIN_USER" -d "password=$ADMIN_PASS" -o /dev/null

echo "=== GET /admin/camera-groups (admin) — debe regresar lista ==="
curl -s -b "$COOKIE" http://localhost:5000/admin/camera-groups | python3 -m json.tool

echo "=== GET /admin/camera-groups SIN cookie (debe redirect a login) ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:5000/admin/camera-groups
```

Expected: con cookie admin, `{"groups": []}`; sin cookie, HTTP 302 (redirect a /login).

---

## Task 9: admin.html — Tab "Grupos" (HTML + tabs wiring)

**Files:**
- Modify: `/root/anpr-camera-dev/app/templates/admin.html`

- [ ] **Step 9.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/templates/admin.html /tmp/admin.html
sudo chown gabriel:gabriel /tmp/admin.html
```

- [ ] **Step 9.2: Inspeccionar el archivo para localizar tabs existentes + ubicación de modals**

Run:
```bash
grep -nE 'data-tab|switchTab|class=".*tab-|<div id="tab-|loadSessions|loadUsers' /tmp/admin.html | head -30
```

Esto te dice cómo están armadas las tabs existentes (Sessions, Users). Reusar el mismo patrón visual y JS.

- [ ] **Step 9.3: Agregar el botón de la tab "Grupos" en la barra de tabs**

Buscar el botón "Users" de la tab bar — es algo como `<button data-tab="users" ...>Users</button>` o similar. Inmediatamente DESPUÉS de ese botón, agregar uno nuevo:

```html
                <button data-tab="groups"
                    class="tab-inactive py-3 px-6 text-sm font-medium transition-colors duration-200"
                    onclick="switchTab('groups')">
                    Grupos
                </button>
```

(Mantén la misma class y onclick que el botón Users; solo cambia el data-tab y el texto visible.)

- [ ] **Step 9.4: Agregar el contenedor del tab "Groups"**

Buscar el último `<div id="tab-users" class="hidden">...</div>` (o como esté nombrado el contenedor de Users). Inmediatamente DESPUÉS, agregar:

```html
        <!-- ============ Tab: Groups ============ -->
        <div id="tab-groups" class="hidden">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-xl font-semibold text-gray-800 dark:text-gray-100">Grupos de cámaras</h2>
                    <button onclick="showCreateGroupModal()"
                        class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium">
                        + Crear grupo
                    </button>
                </div>
                <div class="overflow-x-auto">
                    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                        <thead class="bg-gray-50 dark:bg-gray-700">
                            <tr>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Nombre</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Descripción</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Cámaras</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Viewers</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Acciones</th>
                            </tr>
                        </thead>
                        <tbody id="groupsTableBody" class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                            <tr><td colspan="5" class="text-center p-6 text-gray-500">Cargando...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ============ Modal: Crear/Editar Grupo ============ -->
        <div id="groupModal" class="hidden fixed inset-0 modal-overlay flex items-center justify-center z-50">
            <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-6 w-full max-w-lg mx-4">
                <h3 id="groupModalTitle" class="text-lg font-semibold mb-4 text-gray-800 dark:text-gray-100">Crear grupo</h3>
                <input type="hidden" id="groupModalId" value="">
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Nombre</label>
                    <input type="text" id="groupModalName"
                        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
                        placeholder="Ej. Residencial X" maxlength="255">
                </div>
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Descripción (opcional)</label>
                    <textarea id="groupModalDescription" rows="2"
                        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
                        placeholder="Descripción libre"></textarea>
                </div>
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Cámaras en este grupo</label>
                    <div id="groupModalCameras" class="border border-gray-300 dark:border-gray-600 rounded-md p-3 max-h-48 overflow-y-auto bg-white dark:bg-gray-700">
                        <p class="text-gray-500 text-sm">Cargando cámaras...</p>
                    </div>
                </div>
                <div id="groupModalError" class="hidden text-red-600 text-sm mb-3"></div>
                <div class="flex justify-end space-x-2">
                    <button onclick="hideGroupModal()" class="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-200">Cancelar</button>
                    <button onclick="saveGroup()" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md">Guardar</button>
                </div>
            </div>
        </div>
```

- [ ] **Step 9.5: Extender la función `switchTab` JS para que reconozca "groups"**

Buscar la función `function switchTab(tab) {` en el `<script>`. Suele tener un mapa o un if/else que muestra/oculta los tabs. Localizar la línea que oculta `tab-users` y muestra el seleccionado. Agregar a esa lógica el caso "groups": cuando se selecciona, mostrar `#tab-groups` y ocultar los demás, y al activarse llamar `loadGroups()`.

Si la función usa un patrón generic como `document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.add('hidden'))` y luego `document.getElementById('tab-' + tab).classList.remove('hidden')`, NO hace falta tocarla — el ID `tab-groups` ya coincide con el patrón.

En ese caso, solo agregar AL FINAL del switchTab (antes del `}` de cierre) una llamada condicional:

```javascript
            if (tab === 'groups') {
                loadGroups();
            }
```

- [ ] **Step 9.6: Validar HTML básico (no romper el archivo)**

Run:
```bash
grep -c "</div>" /tmp/admin.html
grep -c "<div" /tmp/admin.html
```

Los dos números deben ser iguales (cada `<div>` cierra). Si difieren por mucho, revisar.

- [ ] **Step 9.7: Deploy + smoke visual**

Run:
```bash
sudo cp /tmp/admin.html /root/anpr-camera-dev/app/templates/admin.html
sudo chown root:root /root/anpr-camera-dev/app/templates/admin.html
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Luego, abrir `/admin` en el navegador como admin: debe aparecer el nuevo tab "Grupos" pero al hacer click la tabla queda en "Cargando..." porque aún no implementamos `loadGroups()`. Eso se hace en Task 10.

---

## Task 10: admin.html — JS para CRUD de grupos (loadGroups, modales, save, delete)

**Files:**
- Modify: `/root/anpr-camera-dev/app/templates/admin.html` (agregar funciones JS dentro del `<script>` existente)

- [ ] **Step 10.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/templates/admin.html /tmp/admin.html
sudo chown gabriel:gabriel /tmp/admin.html
```

- [ ] **Step 10.2: Agregar las funciones JS al final del bloque `<script>` existente**

Buscar el último `</script>` del archivo. Insertar INMEDIATAMENTE ANTES (dentro del script):

```javascript
        // ============ Camera Groups (admin) ============

        let cachedCameras = null;
        let currentGroupCameras = new Set();

        async function loadGroups() {
            try {
                const res = await fetch('/admin/camera-groups');
                const data = await res.json();
                const tbody = document.getElementById('groupsTableBody');
                tbody.innerHTML = '';
                if (!data.groups || data.groups.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="text-center p-6 text-gray-500">No hay grupos creados aún.</td></tr>';
                    return;
                }
                for (const g of data.groups) {
                    const desc = g.description ? escHtml(g.description) : '<span class="text-gray-400 italic">—</span>';
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td class="px-4 py-2 text-sm font-medium text-gray-900 dark:text-gray-100">${escHtml(g.name)}</td>
                        <td class="px-4 py-2 text-sm text-gray-700 dark:text-gray-300">${desc}</td>
                        <td class="px-4 py-2 text-sm text-gray-700 dark:text-gray-300">${g.camera_count}</td>
                        <td class="px-4 py-2 text-sm text-gray-700 dark:text-gray-300">${g.user_count}</td>
                        <td class="px-4 py-2 text-sm">
                            <button class="text-blue-600 hover:underline mr-3" onclick="showEditGroupModal(${g.id})">Editar</button>
                            <button class="text-red-600 hover:underline" onclick="deleteGroup(${g.id}, '${escHtml(g.name).replace(/'/g, "\\'")}')">Borrar</button>
                        </td>`;
                    tbody.appendChild(tr);
                }
            } catch (e) {
                document.getElementById('groupsTableBody').innerHTML =
                    `<tr><td colspan="5" class="text-center p-6 text-red-600">Error: ${escHtml(String(e))}</td></tr>`;
            }
        }

        async function ensureCameraList() {
            if (cachedCameras) return cachedCameras;
            const res = await fetch('/api/cameras');
            const data = await res.json();
            cachedCameras = data.cameras || [];
            return cachedCameras;
        }

        async function renderCameraCheckboxes(checkedIds) {
            const cameras = await ensureCameraList();
            const container = document.getElementById('groupModalCameras');
            if (cameras.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-sm">No hay cámaras configuradas.</p>';
                return;
            }
            container.innerHTML = cameras.map(c => `
                <label class="flex items-center space-x-2 mb-1">
                    <input type="checkbox" class="camera-cb" value="${c.id}" ${checkedIds.has(c.id) ? 'checked' : ''}>
                    <span class="text-sm text-gray-800 dark:text-gray-100">${escHtml(c.friendly_name)} <span class="text-gray-500">(${escHtml(c.ip_address || '')})</span></span>
                </label>
            `).join('');
        }

        async function showCreateGroupModal() {
            document.getElementById('groupModalTitle').textContent = 'Crear grupo';
            document.getElementById('groupModalId').value = '';
            document.getElementById('groupModalName').value = '';
            document.getElementById('groupModalDescription').value = '';
            document.getElementById('groupModalError').classList.add('hidden');
            currentGroupCameras = new Set();
            await renderCameraCheckboxes(currentGroupCameras);
            document.getElementById('groupModal').classList.remove('hidden');
        }

        async function showEditGroupModal(groupId) {
            document.getElementById('groupModalTitle').textContent = 'Editar grupo';
            document.getElementById('groupModalId').value = String(groupId);
            document.getElementById('groupModalError').classList.add('hidden');
            const [gRes, mRes] = await Promise.all([
                fetch(`/admin/camera-groups/${groupId}`),
                fetch(`/admin/camera-groups/${groupId}/cameras`)
            ]);
            const g = await gRes.json();
            const m = await mRes.json();
            document.getElementById('groupModalName').value = g.name || '';
            document.getElementById('groupModalDescription').value = g.description || '';
            currentGroupCameras = new Set((m.cameras || []).map(c => c.id));
            await renderCameraCheckboxes(currentGroupCameras);
            document.getElementById('groupModal').classList.remove('hidden');
        }

        function hideGroupModal() {
            document.getElementById('groupModal').classList.add('hidden');
        }

        async function saveGroup() {
            const id = document.getElementById('groupModalId').value;
            const name = document.getElementById('groupModalName').value.trim();
            const description = document.getElementById('groupModalDescription').value.trim() || null;
            const errEl = document.getElementById('groupModalError');
            if (!name) {
                errEl.textContent = 'El nombre es requerido.';
                errEl.classList.remove('hidden');
                return;
            }
            errEl.classList.add('hidden');

            // 1) Create or update the group itself
            let groupId;
            try {
                if (id) {
                    const res = await fetch(`/admin/camera-groups/${id}`, {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({name, description})
                    });
                    if (!res.ok) {
                        const e = await res.json();
                        throw new Error(e.error || `HTTP ${res.status}`);
                    }
                    groupId = parseInt(id, 10);
                } else {
                    const res = await fetch('/admin/camera-groups', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({name, description})
                    });
                    if (!res.ok) {
                        const e = await res.json();
                        throw new Error(e.error || `HTTP ${res.status}`);
                    }
                    const j = await res.json();
                    groupId = j.id;
                }
            } catch (e) {
                errEl.textContent = `Error guardando grupo: ${String(e.message || e)}`;
                errEl.classList.remove('hidden');
                return;
            }

            // 2) Set cameras for the group
            const checkedIds = Array.from(document.querySelectorAll('.camera-cb:checked')).map(cb => parseInt(cb.value, 10));
            try {
                const res = await fetch(`/admin/camera-groups/${groupId}/cameras`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({camera_ids: checkedIds})
                });
                if (!res.ok) {
                    const e = await res.json();
                    throw new Error(e.error || `HTTP ${res.status}`);
                }
            } catch (e) {
                errEl.textContent = `Grupo guardado, pero error asignando cámaras: ${String(e.message || e)}`;
                errEl.classList.remove('hidden');
                return;
            }

            hideGroupModal();
            loadGroups();
        }

        async function deleteGroup(groupId, name) {
            if (!confirm(`¿Borrar grupo "${name}"? Los viewers que tengan SOLO este grupo asignado quedarán sin acceso.`)) return;
            try {
                const res = await fetch(`/admin/camera-groups/${groupId}`, {method: 'DELETE'});
                if (!res.ok) {
                    const e = await res.json();
                    throw new Error(e.error || `HTTP ${res.status}`);
                }
                loadGroups();
            } catch (e) {
                alert(`Error borrando grupo: ${String(e.message || e)}`);
            }
        }
```

(Asume que `escHtml` ya existe en el `<script>` del archivo — fue agregado en una iteración previa para sanitizar nombres de usuarios en la UI. Si no existe, agrégalo: `function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }` justo antes del bloque nuevo.)

- [ ] **Step 10.3: Deploy + smoke visual**

Run:
```bash
sudo cp /tmp/admin.html /root/anpr-camera-dev/app/templates/admin.html
sudo chown root:root /root/anpr-camera-dev/app/templates/admin.html
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
```

En el navegador como admin → `/admin` → tab "Grupos":
- Lista debe cargar (vacía si no hay grupos).
- Click "+ Crear grupo" abre modal, permite escribir nombre + checkbox cámaras + guardar.
- Editar y Borrar funcionan.

Si hay errores JS, abrir DevTools console.

---

## Task 11: admin.html — Extender modal de viewer con multi-select de grupos

**Files:**
- Modify: `/root/anpr-camera-dev/app/templates/admin.html`

- [ ] **Step 11.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/templates/admin.html /tmp/admin.html
sudo chown gabriel:gabriel /tmp/admin.html
```

- [ ] **Step 11.2: Identificar el modal de viewer**

Run:
```bash
grep -nE "createUser|editUser|userModal|reset-password" /tmp/admin.html | head -20
```

Localiza el modal usado para crear/editar viewer (busca id como `createUserModal` o `userModal`). Necesitas conocer:
- El ID del modal
- Las funciones JS que lo abren (`showCreateUserModal` o similar)
- La función JS que hace el `POST`/`PUT` (`createUser` o `updateUser`)

- [ ] **Step 11.3: Agregar bloque de "Grupos asignados" en el modal de viewer**

En el HTML del modal de Crear/Editar viewer, después de los campos de username/password (antes de los botones Cancelar/Guardar), insertar:

```html
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Grupos asignados</label>
                    <div id="userModalGroups" class="border border-gray-300 dark:border-gray-600 rounded-md p-3 max-h-48 overflow-y-auto bg-white dark:bg-gray-700">
                        <p class="text-gray-500 text-sm">Cargando grupos...</p>
                    </div>
                    <p id="userModalNoGroupsWarning" class="hidden mt-2 text-sm text-yellow-700 dark:text-yellow-400">
                        ⚠️ Sin grupos asignados, este viewer no verá ninguna cámara.
                    </p>
                </div>
```

(Si el modal de "Crear" y el de "Editar" son distintos elementos, repetir el bloque en ambos — el ID `userModalGroups` debe ser único; si hace falta, usa `createUserModalGroups` y `editUserModalGroups`. Para simplificar este plan asumimos UN solo modal que es reutilizado.)

- [ ] **Step 11.4: Agregar JS helpers para cargar grupos disponibles + render checkboxes**

Dentro del `<script>`, agregar (junto a las otras funciones de admin de usuarios):

```javascript
        let cachedAllGroups = null;

        async function ensureAllGroupsList() {
            if (cachedAllGroups) return cachedAllGroups;
            const res = await fetch('/admin/camera-groups');
            const data = await res.json();
            cachedAllGroups = data.groups || [];
            return cachedAllGroups;
        }

        async function renderUserGroupCheckboxes(checkedIds) {
            const allGroups = await ensureAllGroupsList();
            const container = document.getElementById('userModalGroups');
            const warningEl = document.getElementById('userModalNoGroupsWarning');
            if (allGroups.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-sm">No hay grupos creados aún. Crea grupos en la pestaña "Grupos" antes de asignar.</p>';
                warningEl.classList.add('hidden');
                return;
            }
            container.innerHTML = allGroups.map(g => `
                <label class="flex items-center space-x-2 mb-1">
                    <input type="checkbox" class="user-group-cb" value="${g.id}" ${checkedIds.has(g.id) ? 'checked' : ''} onchange="updateUserGroupsWarning()">
                    <span class="text-sm text-gray-800 dark:text-gray-100">${escHtml(g.name)}</span>
                </label>
            `).join('');
            updateUserGroupsWarning();
        }

        function updateUserGroupsWarning() {
            const checked = document.querySelectorAll('.user-group-cb:checked').length;
            const warningEl = document.getElementById('userModalNoGroupsWarning');
            if (checked === 0) {
                warningEl.classList.remove('hidden');
            } else {
                warningEl.classList.add('hidden');
            }
        }

        async function fetchUserGroupAssignments(userId) {
            const res = await fetch(`/admin/users/${userId}/camera-groups`);
            const data = await res.json();
            return new Set((data.groups || []).map(g => g.id));
        }

        async function saveUserGroupAssignments(userId) {
            const checkedIds = Array.from(document.querySelectorAll('.user-group-cb:checked'))
                .map(cb => parseInt(cb.value, 10));
            const res = await fetch(`/admin/users/${userId}/camera-groups`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({group_ids: checkedIds})
            });
            if (!res.ok) {
                const e = await res.json();
                throw new Error(e.error || `HTTP ${res.status}`);
            }
        }
```

- [ ] **Step 11.5: Wire-in: cargar checkboxes al abrir el modal**

Localizar las funciones existentes que abren el modal:
- `showCreateUserModal()` → al final, antes de hacer visible el modal, agregar `await renderUserGroupCheckboxes(new Set());`
- La función que abre el modal de edit (o equivalente) → cargar primero los grupos del usuario y luego renderizar checkboxes con esos checked.

Ejemplo de wire-in para edit (ajustar según nombres reales):

```javascript
        async function showEditUserModal(userId) {
            // ... código existente para llenar username, etc. ...
            const checked = await fetchUserGroupAssignments(userId);
            await renderUserGroupCheckboxes(checked);
            // ... mostrar modal ...
        }
```

(Si el modal de edición no existe como tal en el código actual y los viewers solo se crean/borran/reset-password, agregar una función `showEditUserModal` que llame al endpoint GET de detalle del usuario + abre el modal. Para este plan, asumir que ya existe — verificar al revisar el archivo.)

- [ ] **Step 11.6: Wire-in: guardar asignación de grupos al guardar el usuario**

En la función JS que guarda el usuario (POST para crear, PUT para editar), AGREGAR después del save exitoso del usuario:

```javascript
            // After the user is created/updated, save group assignments
            try {
                await saveUserGroupAssignments(userId);  // userId del response del POST o del modal
            } catch (e) {
                alert(`Usuario guardado, pero error asignando grupos: ${String(e.message || e)}`);
            }
```

Adaptar el nombre `userId` al contexto (en crear, viene del response del POST; en edit, viene del id del modal).

- [ ] **Step 11.7: Deploy + smoke visual**

Run:
```bash
sudo cp /tmp/admin.html /root/anpr-camera-dev/app/templates/admin.html
sudo chown root:root /root/anpr-camera-dev/app/templates/admin.html
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
```

En el navegador como admin:
- Tab Users → editar un viewer existente → debe aparecer la sección "Grupos asignados" con los grupos que existan, ninguno marcado (porque ningún viewer está asignado todavía).
- Marcar un par, guardar.
- Volver a abrir → los marcados aparecen aún marcados.
- Si quedan 0 marcados, el warning amarillo aparece.

---

## Task 12: Validación end-to-end + commit final

**Files:** ninguno modificado — solo verificación

- [ ] **Step 12.1: Setup de prueba — crear un grupo y asignar a un viewer**

En el navegador como admin:
1. Tab Grupos → Crear grupo "TestSmokeIntegration", marcar solo CAM1 (Cinco Ventanas).
2. Tab Users → Editar `visorjorgecarrillo` → asignar "TestSmokeIntegration" → Guardar.

- [ ] **Step 12.2: Login como ese viewer en pestaña incógnita y verificar acceso restringido**

En una pestaña incógnita:
1. Login como `visorjorgecarrillo` con su password.
2. Confirmar que el dropdown de cámaras muestra solo "Cinco Ventanas".
3. Confirmar que la tabla solo muestra eventos de CAM1.
4. Confirmar que el polling de nuevos eventos no notifica eventos de otras cámaras.

- [ ] **Step 12.3: Verificar bloqueo de imagen no autorizada**

Anotar `image_filename` de un evento de CAM4 (visto desde la sesión admin). En la pestaña del viewer, intentar abrir directamente `https://<host>/images/<ese_filename>`.

Expected: HTTP 403.

Hacer lo mismo con un `image_filename` de CAM1 (debe servir OK).

- [ ] **Step 12.4: Verificar admin no afectado**

En la sesión admin:
- Dropdown muestra las 4 cámaras.
- Tabla muestra eventos de todas.
- Imágenes de cualquier cámara se sirven sin 403.

- [ ] **Step 12.5: Logs limpios**

Run:
```bash
sudo docker logs --since=10m anpr-db-manager 2>&1 | grep -iE "error|exception|traceback" | tail
sudo docker logs --since=10m anpr-web 2>&1 | grep -iE "error|exception|traceback" | tail
```

Expected: vacío.

- [ ] **Step 12.6: Limpieza del setup de prueba**

En el navegador como admin:
1. Tab Users → editar `visorjorgecarrillo` → desasignar "TestSmokeIntegration".
2. Tab Grupos → borrar "TestSmokeIntegration".

Luego comenzar el setup REAL siguiendo el runbook `docs/deployment/camera-groups-migration.md` Paso 3-5 (crear los grupos productivos, asignar los 4 viewers reales).

- [ ] **Step 12.7: Commit final**

Run:
```bash
sudo git -C /root/anpr-camera-dev status --short
sudo git -C /root/anpr-camera-dev diff HEAD -- app/anpr_db_manager.py app/anpr_web.py app/templates/admin.html | wc -l
echo ""
sudo git -C /root/anpr-camera-dev add app/anpr_db_manager.py app/anpr_web.py app/templates/admin.html
sudo git -C /root/anpr-camera-dev status --short
sudo git -C /root/anpr-camera-dev commit -m "$(cat <<'EOF'
feat(groups): camera groups with per-viewer access control

Schema:
- New tables camera_groups, camera_group_members (M:N cameras↔groups),
  user_camera_groups (M:N user↔groups). All with FK CASCADE.
- initialize_database() creates them idempotently with CREATE TABLE
  IF NOT EXISTS. No data seeding.

db-manager (app/anpr_db_manager.py):
- _parse_allowed_camera_ids() helper: None=no filter, []=deny all,
  [ids]=filter to set.
- Applied to /api/cameras, /api/events, /api/events/latest_timestamp.
- New admin endpoints under /api/admin/camera-groups/* and
  /api/admin/users/<id>/camera-groups for CRUD + membership management.

anpr-web (app/anpr_web.py):
- get_allowed_camera_ids(user): None for admin, list of ints for viewer,
  [] for viewer without group assignments.
- api_proxy injects allowed_camera_ids query param for viewers when
  forwarding requests to db-manager. Admin requests forwarded unmodified.
- serve_image validates the requested filename's event camera_id is in
  the user's allowed list before serving (admin bypass).
- Mirror admin endpoints with @admin_required, proxy to db-manager.

admin.html:
- New tab "Grupos" with CRUD UI: create/edit groups, multi-select of
  cameras per group, delete with confirmation.
- Extended viewer modal with "Grupos asignados" multi-select and
  warning when zero groups selected.

Viewer default: no groups = no access (strict). Admins bypass groups
entirely. config.ini still source of truth for cameras; cameras table
sync is unchanged.

Production migration runbook: docs/deployment/camera-groups-migration.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 12.8: Push al remoto**

Run:
```bash
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" \
  git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master
sudo git -C /root/anpr-camera-dev fetch origin
sudo git -C /root/anpr-camera-dev status --short --branch | head -3
```

Expected: push exitoso, branch sincronizado con origin.

---

## Rollback

Si cualquier task falla irrecuperablemente, seguir el runbook `docs/deployment/camera-groups-migration.md` sección "Rollback". Tres niveles según severidad: solo código, código + drop tables, restore completo de DB. Los respaldos del Task 1 son la red de seguridad.

---

## Self-review check

Antes de empezar la implementación, verificar:

- **Spec coverage**: cada sección 4-5 del spec (schema, endpoints filter, admin endpoints, anpr-web helper, anpr-web proxy, serve_image, admin UI) tiene su task. Sí: Task 2 cubre schema, Task 3 cubre filter en 3 endpoints, Task 4-5 cubren admin endpoints en db-manager, Task 6-8 cubren anpr-web, Task 9-11 cubren admin UI.
- **Placeholders**: no hay TBD/TODO/etc en el plan.
- **Type consistency**: `allowed_camera_ids` consistente (siempre comma-separated string en URL, list[int] en Python). `group_ids`/`camera_ids` arrays de int en JSON. `get_allowed_camera_ids` retorna `None | list[int]`. `_parse_allowed_camera_ids` retorna `None | list[int]`. Coinciden.
- **Code blocks**: cada step que cambia código incluye el código completo, no pseudocódigo.
- **Verificaciones explícitas**: cada task tiene smoke test con expected output específico.
- **TDD-style aplicado**: cada step verifica lo que hizo antes de pasar al siguiente.
- **Commits**: el plan hace UN solo commit al final (Step 12.7) porque las tareas son funcionalmente interdependientes (la UI no funciona sin los endpoints, los endpoints sin el schema). Para revisión incremental se puede dividir si se prefiere — anotación al equipo de implementación si quieren dividir el commit.
