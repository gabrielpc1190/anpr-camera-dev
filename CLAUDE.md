# CLAUDE.md — ANPR Camera System

Guía de referencia rápida para futuras sesiones trabajando en este proyecto.

## 1. Propósito del sistema

Sistema ANPR (Automatic Number Plate Recognition) en producción 24/7 que:
- Captura eventos de placas vehiculares desde cámaras Dahua IP vía NetSDK.
- Persiste eventos + imágenes en MariaDB.
- Expone una interfaz web (Flask) protegida con autenticación para visualizar/filtrar eventos.
- Se publica al exterior opcionalmente vía Cloudflare Tunnel.

Lenguaje: Python 3.11. Despliegue: Docker Compose con `network_mode: host` (LXC-compatible).

## 2. Arquitectura — 4 contenedores + tunnel

```
┌──────────────┐   HTTP POST /event     ┌──────────────────┐    SQL    ┌──────────┐
│ anpr-listener├──(image + JSON)───────▶│ anpr-db-manager  ├──────────▶│ mariadb  │
└──────┬───────┘                        └────────┬─────────┘           └──────────┘
       │ Dahua NetSDK                            │ REST API
       ▼                                         ▲
   Cámaras IP                                    │
                                          ┌──────┴──────┐
                                          │  anpr-web   │◀──── Cloudflare Tunnel
                                          └─────────────┘
```

Todos los servicios corren con `network_mode: host`, por eso usan `localhost` para hablarse:
- `anpr-listener` → `http://localhost:5001/event` ([docker-compose.yml:14](docker-compose.yml#L14))
- `anpr-web` → `http://localhost:5001` (DB_MANAGER_API_URL)
- Cloudflare debe apuntar a `http://127.0.0.1:5000`, **no** `http://anpr-web:5000`.

**Fuente de verdad de cámaras:** La tabla `cameras` en MariaDB es la fuente canónica de identidad de cada cámara. El `anpr-db-manager` la sincroniza desde `config.ini` al arrancar (`initialize_database`). Todos los eventos (`anpr_events`) referencian `cameras.id` como FK entero, en lugar de depender del `FriendlyName` como identificador.

## 3. Estructura del repositorio

```
/root/anpr-camera-dev/
├── README.md, ROADMAP.md, SECURITY_AUDIT.md
├── docker-compose.yml          # Orquesta los 5 servicios
├── anpr_listener.Dockerfile    # Instala SDK Dahua (.whl) + libs C
├── anpr_db_manager.Dockerfile  # Flask + Gunicorn (puerto 5001)
├── anpr_web.Dockerfile         # Flask + Gunicorn (puerto 5000) — COPY de translations/
├── requirements.txt            # Deps Python comunes a los 3 servicios
├── .env / .env.example         # Secretos: passwords MariaDB, CLOUDFLARE_TOKEN
├── setup.sh                    # CLI de operación (start/stop/users/etc.)
├── backups/                    # Dumps SQL + tars de código pre-deploy (sin tracker en git)
├── docs/
│   ├── deployment/             # Runbooks operacionales (ej. camera-groups-migration.md)
│   ├── field-investigations.md # Anomalías de hardware/config pendientes en sitio
│   └── superpowers/
│       ├── specs/              # Specs de features (camera_id, groups, i18n, image-retention)
│       └── plans/              # Planes operacionales paso a paso por feature
└── app/
    ├── anpr_listener.py        # ~316 líneas — captura eventos Dahua
    ├── anpr_db_manager.py      # ~900 líneas — API Flask + DB (incluye CRUD de groups)
    ├── anpr_web.py             # ~600 líneas — UI Flask + auth + proxy + i18n + admin groups
    ├── models.py               # SQLAlchemy User model + bcrypt
    ├── config.ini              # Cámaras + logging (NO en git)
    ├── config.ini.example
    ├── NetSDK-*.whl            # SDK Dahua (descargado por setup.sh)
    ├── anpr_images/            # JPGs persistidos por db-manager
    ├── logs/                   # anpr_listener.log, anpr_db_manager.log
    ├── db/                     # Bind-mount de MariaDB (datafiles)
    ├── static/                 # tailwind.js, inter.css, favicon
    ├── translations/           # i18n — fuente de verdad: es.json + en.json (176 keys c/u)
    │   ├── es.json
    │   └── en.json
    └── templates/
        ├── index.html          # Dashboard (520 líneas, todas las strings vía t())
        ├── admin.html          # Panel admin (794+ líneas, 3 tabs incluyendo Grupos)
        └── login.html          # Toggle ES/EN visible (sin sesión)
```

## 4. Componentes principales — puntos de entrada

### 4.1 `app/anpr_listener.py` — captura de eventos
- [main()](app/anpr_listener.py#L162) — carga `config.ini`, inicia logger, conecta cámaras y arranca loop de salud (60 s).
- [connect_camera()](app/anpr_listener.py#L166) — login con `LoginWithHighLevelSecurity` + suscripción `RealLoadPictureEx` a `EM_EVENT_IVS_TYPE.TRAFFICJUNCTION`.
- [make_analyzer_callback()](app/anpr_listener.py#L147) — fábrica que devuelve un callback ctypes dedicado por cámara. La identidad de la cámara viaja por **closure** (`cam_info`), no por handle/dwUser del SDK. Esto resuelve la mis-atribución cuando cámaras comparten IP externa bajo NAT.
- [_process_event()](app/anpr_listener.py#L70) — extrae placa, dirección, tipo, color, guarda JPG temporal, construye payload con `CameraId` (int) + `CameraID` (FriendlyName legacy) y dispara `send_event_async`. Lee `alarm_info.UTC` una sola vez al inicio de la función.
- [send_event_async()](app/anpr_listener.py#L26) — POST multipart al `anpr-db-manager` en thread daemon.
- [disconnect_callback()](app/anpr_listener.py#L54) — al desconectar marca `login_id=0` para que el health loop reintente. Resuelve la cámara por `login_id` (único por sesión) en lugar de por IP.
- **Self-healing**: el loop en [main()](app/anpr_listener.py#L296) llama `connect_camera()` cada 60 s para cualquier cámara con `login_id == 0`.

### 4.2 `app/anpr_db_manager.py` — Flask API (puerto 5001)
Single source of truth de la base de datos. Endpoints:

**Endpoints de lectura (filtrables por `allowed_camera_ids`)**:
- `POST /event` — `receive_event()` — recibe multipart del listener, guarda imagen e inserta fila.
- `GET /api/events` — `get_events()` — paginado + filtros (placa, camera_id INT o camera_friendly_name string, fecha/hora, vehicle_type, access_status, driving_direction). Respuesta incluye `camera_id` (INT) y `camera_friendly_name` (string).
- `GET /api/cameras` — `get_cameras()` — devuelve `[{id, friendly_name, ip_address, port}, ...]` desde la tabla `cameras`.
- `GET /api/events/latest_timestamp` — `get_latest_timestamp()` — usado por el dashboard para detectar eventos nuevos (polling).
- `GET /health` — usado por el healthcheck de Docker Compose.

**Filter helper `_parse_allowed_camera_ids()`** — los 3 endpoints de lectura aceptan opcionalmente el query param `allowed_camera_ids=1,3,7`:
- Ausente → no filtro (path admin, ve todo).
- Presente vacío (`?allowed_camera_ids=`) → "deny all" (devuelve lista vacía / 0 eventos).
- Presente con valores → `WHERE camera_id IN (...)`.

El cliente es anpr-web, que computa el filtro por viewer (ver 4.3). db-manager no conoce identidades de usuario.

**Endpoints admin de grupos** (todos bajo `/api/admin/...`, sin auth directa — anpr-web los envuelve con `@admin_required`):
- `GET/POST /api/admin/camera-groups` — list (con cuenta de cameras+users por grupo) / create
- `GET/PUT/DELETE /api/admin/camera-groups/<id>` — CRUD individual
- `GET/PUT /api/admin/camera-groups/<id>/cameras` — membership cámara↔grupo (PUT reemplaza la lista completa)
- `GET/PUT /api/admin/users/<id>/camera-groups` — asignaciones user↔grupo (PUT reemplaza la lista completa)

**Schema management**:
- `initialize_database()` — crea/migra al startup: `anpr_events`, `cameras`, `camera_groups`, `camera_group_members`, `user_camera_groups`. Usa `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` y `CREATE TABLE IF NOT EXISTS` para idempotencia. Sincroniza cámaras desde `config.ini`.
- `insert_anpr_event_db()` — INSERT con `camera_id` (INT FK) y `camera_friendly_name` (VARCHAR). Verifica FK contra `cameras` antes de insertar.

### 4.3 `app/anpr_web.py` — UI Flask (puerto 5000)

**Auth + sesiones**:
- Flask-Login + sesiones server-side en MariaDB vía `flask-session` con `SESSION_TYPE='sqlalchemy'`. Lifetime 2 h. Decorador `@admin_required`.
- Decodifica sesiones con `msgspec.msgpack` para asociar `_user_id` → username/role.
- **IP de sesión**: capturada en `login()` y guardada en el dict de session (`session['ip_address'] = ip`). flask-session la persiste atómicamente con el resto del payload. El admin UI la lee del payload decodificado (con fallback a la columna `sessions.ip_address` para filas históricas). *Patrón anterior — UPDATE directo a la columna — tenía race condition que dejaba IPs en NULL.*

**i18n (v2.6)**:
- Al startup carga `TRANSLATIONS` desde `app/translations/{es,en}.json` (fail-fast si faltan/corruptos).
- `SUPPORTED_LANGS = ('es', 'en')`, `DEFAULT_LANG = 'es'`.
- Helpers `get_lang()` (lee cookie validada) y `t(key)` (lookup con fallback a key literal).
- `@app.context_processor` `inject_i18n` expone en cada template: `t`, `current_lang`, `translations_json`.
- `GET /set-lang/<lang>?next=<url>` — setea cookie `lang` (1 año, SameSite=Lax) + redirect. Open-redirect protegido por validación de mismo-host.
- Mensajes `flash()` y `jsonify({'error': ...})` usan `t(key)` — backend traduce al idioma activo del request.
- Strings con interpolación: keys con `{name}` invocadas con `.format(name=value)` en Python o `.replace('{name}', name)` en JS.

**Control de acceso por grupos de cámaras (v2.5)**:
- `get_allowed_camera_ids(user)` — devuelve `None` para admin (sin filtro), `list[int]` para viewer con grupos, `[]` para viewer sin grupos. Query: JOIN `camera_group_members` ↔ `user_camera_groups` por `user_id`.
- `api_proxy()` (`/api/<path>`) — para viewer, inyecta `allowed_camera_ids=<csv>` en el query string al forwardear al db-manager. Para admin, pasa el request sin tocar.
- `serve_image(filename)` — antes de servir el JPG, si el usuario es viewer hace lookup del `camera_id` del evento (por `image_filename`) y valida que está en `allowed_camera_ids`. Admin: bypass.

**Rutas vista**: `/login`, `/logout`, `/` (index), `/admin`, `/set-lang/<lang>`.

**Rutas admin (todas con `@admin_required`)**:
- `/admin/sessions` (GET/DELETE/revoke-all)
- `/admin/users` (CRUD; sólo usuarios `viewer` — los `admin` se gestionan por CLI)
- `/admin/camera-groups` (CRUD + `/<id>/cameras`) — espejos thin proxy de los `/api/admin/...` del db-manager
- `/admin/users/<id>/camera-groups` (GET/PUT) — proxy a `/api/admin/users/<id>/camera-groups`
- Helper interno `_proxy_to_db_manager(method, path_suffix, json_body)` para todos los espejos admin.

**Política de password**: `is_password_strong()` exige 10+ chars, mayúscula, minúscula, dígito (no especial). *Inconsistencia conocida con `setup.sh` que sí exige especial.*

### 4.4 `app/models.py` — modelo de usuario
- [User](app/models.py#L7) — campos: `id`, `username`, `password` (bcrypt hash), `role` ('admin' | 'viewer').
- `is_admin` → `role == 'admin'`. `set_password()` / `check_password()` con `bcrypt`.

## 5. Configuración

### `.env` (raíz, NO commitear)
- `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `MYSQL_USER=anpr_user`, `MYSQL_DATABASE=anpr_events`, `DB_HOST=127.0.0.1`
- `CLOUDFLARE_TOKEN`
- `SECRET_KEY` (Flask) — si no está, anpr_web usa `'dev_key_please_change_in_prod'`. **Mejora pendiente**: forzar el set.

### `app/config.ini` (NO commitear)
- `[General]` — `LogLevel` (0-3, default 2 INFO; producción usa 1 WARNING), `LogDirectory`.
- `[Paths]` — `ImageDirectory`.
- `[DahuaSDK]` — `DefaultUsername`, `DefaultPassword`, `DefaultPort` (37777).
- `[Camera.<Name>]` — `Enabled`, **`Id` (int, requerido)**, `IPAddress`, `FriendlyName`, opcionales `Username/Password/Port`. Sólo se cargan cámaras con `Enabled = true`.
  - `Id` es el identificador único entero de la cámara — PK en la tabla `cameras` y FK en `anpr_events`. Debe ser único entre todas las cámaras. Si falta o no es entero, el listener salta esa cámara con error claro y no falla silenciosamente.
- Cámaras activas actuales: `CAM1` (Id=1, 10.45.14.11, "Cinco Ventanas"), `CAM2` (Id=2, 10.45.14.12, "Las Brisas").

Ejemplo de sección de cámara:
```ini
[Camera.CAM1]
Enabled = true
Id = 1
IPAddress = 10.45.14.11
FriendlyName = Cinco Ventanas

[Camera.NAT_A]
Enabled = true
Id = 3
IPAddress = 10.49.9.50
Port = 1177
FriendlyName = Sitio Externo - Entrada

[Camera.NAT_B]
Enabled = true
Id = 4
IPAddress = 10.49.9.50
Port = 1277
FriendlyName = Sitio Externo - Salida
```

### Puertos de red hacia las cámaras

El listener abre **una sola conexión TCP por cámara** al puerto del NetSDK propietario de Dahua. Todo el tráfico (login, suscripción a eventos `TRAFFICJUNCTION` y recepción del buffer JPG) viaja por esa misma conexión.

| Puerto | Protocolo | Dirección | Uso | Referencia |
|---|---|---|---|---|
| **37777** | TCP | servidor ANPR → cámara | NetSDK Dahua: login + eventos + imagen | [anpr_listener.py:176](app/anpr_listener.py#L176) (`EM_LOGIN_SPAC_CAP_TYPE.TCP`), [config.ini:25](app/config.ini#L25) (`DefaultPort = 37777`) |

**No se usan** RTSP (554), HTTP (80), HTTPS (443) ni ningún otro puerto desde este software. Si en el futuro se agregan funciones de streaming/snapshot HTTP, requerirían abrir 80/443 adicionalmente.

#### Cámaras detrás de módems/ONU remotos (port forwarding)

Cuando una cámara está detrás de un módem/ONU en otra ubicación, el servidor ANPR la alcanza por la IP pública del módem. Hay que configurar **port forwarding TCP** en cada módem:

```
WAN:<puerto_publico> ──TCP──▶ <IP_LAN_camara>:37777
```

Dos estrategias según el caso:

1. **Una cámara por ONU (cada módem con IP pública distinta):** mapear `WAN:37777 → CAM:37777` en cada módem. En `config.ini`:
   ```ini
   [Camera.NuevaCam]
   Enabled = true
   Id = 5
   IPAddress = <ip_publica_de_la_ONU>
   FriendlyName = Nombre Sitio
   # Port no hace falta — usa el DefaultPort = 37777
   ```

2. **Varias cámaras detrás del mismo módem (misma IP pública):** mapear puertos externos distintos al 37777 interno de cada cámara, p.ej. `WAN:1177 → CAM_A:37777`, `WAN:1277 → CAM_B:37777`. En `config.ini` hay que **fijar el `Port` por cámara** y asignar `Id` distintos:
   ```ini
   [Camera.CamA]
   Enabled = true
   Id = 3
   IPAddress = <ip_publica_compartida>
   Port = 1177
   FriendlyName = ...

   [Camera.CamB]
   Enabled = true
   Id = 4
   IPAddress = <ip_publica_compartida>
   Port = 1277
   FriendlyName = ...
   ```
   El listener usa un callback closure dedicado por cámara (`make_analyzer_callback`), así que la identidad es correcta incluso si el SDK reutiliza el mismo handle para ambas suscripciones.

#### Requisitos adicionales

- **Firewall del lado del servidor ANPR**: permitir **salida TCP** al puerto correspondiente (37777 por defecto) hacia las IPs públicas de los módems.
- **IP fija o DDNS** del lado de los módems: si la WAN es dinámica, conviene usar un DDNS y poner el hostname (no la IP) en `IPAddress`. `configparser` acepta hostnames sin problema.
- **El puerto NetSDK en la cámara debe estar habilitado**: en la UI de la cámara Dahua, *Network → TCP/IP → Connection* — confirmar "TCP Port" (default 37777). Algunos integradores lo cambian; si fue cambiado, ajustar también el `Port` en `config.ini`.
- **El listener inicia la conexión saliente** — *no* es necesario abrir puertos entrantes en el módem del servidor ANPR (excepto los del web UI/Cloudflare, ya cubiertos por la sección 9 abajo).
- Tras editar `app/config.ini`, **reconstruir y redesplegar** el listener para que cargue las cámaras nuevas (ver sección 7 y la nota en sección 12).

## 6. Esquema de base de datos

### Tabla `cameras` (nueva — v2.4)

Creada/sincronizada por `initialize_database()` en anpr-db-manager al arrancar. Se puebla desde `config.ini`.

| columna | tipo | notas |
|---|---|---|
| id | INT PK | `Id` del `[Camera.X]` en config.ini — entero controlado por el operador |
| friendly_name | VARCHAR(255) UNIQUE NOT NULL | `FriendlyName` de config.ini |
| ip_address | VARCHAR(255) NOT NULL | `IPAddress` |
| port | INT NOT NULL | `Port` efectivo (o DefaultPort) |
| enabled | TINYINT(1) DEFAULT 1 | |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP ... ON UPDATE CURRENT_TIMESTAMP | |

### Tabla principal `anpr_events` ([anpr_db_manager.py:90](app/anpr_db_manager.py#L90))

| columna | tipo | notas |
|---|---|---|
| id | INT AUTO_INCREMENT PK | |
| plate_number | VARCHAR(255) NOT NULL | |
| camera_friendly_name | VARCHAR(255) | `FriendlyName` de config.ini (antes llamada `camera_id`) |
| camera_id | INT NULL FK → cameras.id | Identificador numérico de la cámara. `NULL` para eventos históricos sin match. Índice: `idx_camera_id`. Constraint: `fk_anpr_events_camera`. |
| timestamp | DATETIME NOT NULL | UTC del evento |
| image_filename | VARCHAR(255) | nombre en `/app/anpr_images/` |
| confidence | FLOAT | 0.0–1.0 (se almacena `nConfidence/100`) |
| processed_data | JSON | payload completo (color, marca, velocidad, etc.) |
| vehicle_type | VARCHAR(50) | "MotorVehicle", ... |
| access_status | VARCHAR(50) | "Normal Car" / "Trust Car" / "Suspicious Car" / "Unknown" |
| driving_direction | VARCHAR(50) | "Approaching" / "Leaving" / "Unknown" |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |

**Nota de migración:** 115 461 filas históricas fueron backfilleadas con `camera_id` via JOIN en `camera_friendly_name`. Filas sin `FriendlyName` conocido quedaron en `NULL`.

### Tablas de grupos (v2.5 — control de acceso por grupos)

#### `camera_groups`
| columna | tipo | notas |
|---|---|---|
| id | INT AUTO_INCREMENT PK | |
| name | VARCHAR(255) UNIQUE NOT NULL | nombre humano-legible del grupo |
| description | TEXT NULL | opcional |
| created_at, updated_at | TIMESTAMP | |

#### `camera_group_members` (M:N cámaras↔grupos)
| columna | tipo | notas |
|---|---|---|
| camera_id | INT NOT NULL | FK → `cameras(id)` `ON DELETE CASCADE` |
| group_id | INT NOT NULL | FK → `camera_groups(id)` `ON DELETE CASCADE` |
| **PK compuesta** | `(camera_id, group_id)` | |

#### `user_camera_groups` (M:N usuarios↔grupos)
| columna | tipo | notas |
|---|---|---|
| user_id | INT NOT NULL | FK → `user(id)` `ON DELETE CASCADE` |
| group_id | INT NOT NULL | FK → `camera_groups(id)` `ON DELETE CASCADE` |
| **PK compuesta** | `(user_id, group_id)` | |

**Regla**: viewer sin grupos = ve nada (estricto, default). Admin bypassea grupos por completo.

**Query canónica para "cámaras accesibles a un usuario"**:
```sql
SELECT DISTINCT cgm.camera_id
FROM camera_group_members cgm
JOIN user_camera_groups ucg ON ucg.group_id = cgm.group_id
WHERE ucg.user_id = <user_id>
```

### Tablas auxiliares
- `user` — del modelo `User` (con columna `role` añadida vía ALTER TABLE en startup).
- `sessions` — tabla de sesiones de `flask-session` (data = msgpack; columna `ip_address` legacy, hoy la IP vive dentro de `data`).

## 7. Operación — `setup.sh` y despliegue de código

Wrapper bash sobre `docker compose` ([setup.sh](setup.sh)):

| comando | acción |
|---|---|
| `./setup.sh start` | Instala deps, descarga SDK Dahua (.whl), crea dirs, configura Cloudflare interactivamente, `docker compose up --build -d`, crea admin si no hay usuarios. |
| `./setup.sh stop` | `docker compose down`. |
| `./setup.sh restart` | stop + start. |
| `./setup.sh logs [svc]` | `docker compose logs -f --tail=100`. |
| `./setup.sh clean [--force]` | Down + opcionalmente borra `app/{anpr_images,logs,db}`. **Destructivo**. |
| `./setup.sh update` | `git pull --ff-only` + rebuild + `up -d`. |
| `./setup.sh reset-admin` | Reset interactivo de password (lista usuarios, pide ID + nuevo password). |
| `./setup.sh create-user` | Crea admin o viewer (CLI valida password: 10+ chars, mayús, dígito, especial). |
| `./setup.sh delete-user` | Borra usuario por ID. |
| `./setup.sh rebuild` | `down` + `build` + `up -d`. |
| `./setup.sh reconfigure` | Hace `down` para editar configs. |

### ⚠️ IMPORTANTE: Cómo redesplegar código de listener/db-manager/web

Los archivos `.py` y templates están **baked into la imagen Docker** en el momento del `docker build`. Los bind-mounts solo cubren `config.ini` y `logs/`. Por eso:

- **`docker restart anpr-listener`** — NO recarga código. Reutiliza la imagen existente. Solo sirve para recargar `config.ini`.
- **Para desplegar cambios de código Python o templates:**
  ```bash
  sudo docker-compose build anpr-listener   # reconstruye la imagen
  sudo docker-compose up -d anpr-listener   # reemplaza el contenedor
  ```
  Lo mismo aplica para `anpr-db-manager` y `anpr-web`.

⚠️ La política de password de `setup.sh` exige carácter **especial** (`is_password_strong` en CLI), pero `anpr_web.py:is_password_strong` **no** lo exige. Inconsistencia conocida.

## 8. Flujo end-to-end de un evento

1. Cámara Dahua detecta placa → genera evento `TRAFFICJUNCTION` con buffer JPG.
2. NetSDK dispara el callback dedicado de esa cámara (generado por `make_analyzer_callback`).
3. `_process_event` guarda JPG temporal en `/app/anpr_images/temp_*.jpg` y construye payload con `CameraId` (int) y `CameraID` (FriendlyName).
4. `send_event_async` → POST multipart `http://localhost:5001/event`.
5. `anpr_db_manager.receive_event` renombra/guarda la imagen final (`<ts>_<cam>_<plate>_<uuid>.jpg`), verifica FK contra tabla `cameras`, inserta fila en MariaDB con `camera_id` (INT) y `camera_friendly_name` (VARCHAR).
6. Listener elimina el archivo temporal (en `finally`).
7. Dashboard hace polling `GET /api/events/latest_timestamp` y actualiza la tabla cuando hay eventos nuevos.
8. Click en miniatura → `GET /images/<filename>` (servido por anpr-web con `send_from_directory`).

## 9. Resiliencia y peculiaridades

- **Race condition resuelta** con `depends_on: service_healthy` en docker-compose (mariadb → db-manager → listener).
- **Reconexión de cámaras**: loop de 60 s; no hay backoff exponencial — siempre intenta cada minuto.
- **Fallback de dirección**: si `emCarDrivingDirection` viene en 0/Unknown, el listener lee `szDrivingDirection` ([anpr_listener.py:103](app/anpr_listener.py#L103)).
- **Listener corre como `python` (single-process)**, no Gunicorn. Db-manager: 2 workers Gunicorn. Web: 4 workers × 4 threads Gunicorn.
- **Filtro de health-check en logs**: `HealthCheckFilter` ([anpr_db_manager.py:8](app/anpr_db_manager.py#L8)) silencia las líneas `/health`.
- **Decode**: las cadenas del SDK Dahua se decodifican con `gb2312` (codificación china), con `errors='ignore'`.

## 10. Frontend

### `templates/index.html` — Dashboard de eventos

- TailwindCSS (cargado local: `static/tailwind.js`) + GLightbox (CDN) para zoom de imágenes.
- Modo dark con persistencia en `localStorage.theme`.
- Toggle ES/EN visible en el header (`<a href="/set-lang/...?next={{ request.path }}">`).
- Form de filtros: `plate_number`, `camera_id` (dropdown con valor = `cam.id` int, label = `cam.friendly_name`), `start_date/time`, `end_date/time`, `driving_direction`. **El dropdown `access_status` (Estado) está oculto** (`class="hidden"` en el div contenedor) — el `<select>` sigue en el DOM para no romper el JS que arma el querystring.
- Tabla de eventos muestra `event.camera_friendly_name` (string).
- Polling `checkForNewEvents()` consulta `/api/events/latest_timestamp?since=...` y muestra notificación traducida (`t('notification.new_event')` / `t('notification.new_events_count')`).
- Hotkeys de navegación implementados (paginación rápida).

### `templates/admin.html` — Panel admin

- Tres tabs: **Sessions** (sesiones activas, revoke), **Users** (CRUD de viewers — los admins se gestionan por CLI), **Grupos** (CRUD de grupos de cámaras + asignación de cámaras al grupo).
- Modal de "Crear/Editar viewer" incluye sección "Grupos asignados" (multi-select). Botón "Grupos" por viewer abre modal dedicado `editUserGroupsModal`.
- Botón "Grupos" en cada viewer también opera por la API admin.
- Toggle ES/EN en el header igual que index.html.

### `templates/login.html` — Login

- Toggle ES/EN visible antes del formulario (los clientes externos pueden elegir idioma sin sesión).
- Mensaje flash de "credenciales inválidas" pasa por `t('login.invalid')`.

### Patrón i18n del frontend

- `<head>` de cada template inyecta `<script>window.__i18n = {{ translations_json | safe }};</script>` con el dict del idioma activo.
- Cada `<script>` de aplicación define al inicio `function t(key) { return window.__i18n[key] || key; }`.
- Strings dinámicos que vienen del backend en inglés (`Trust Car`, `Approach`, `White`, etc.) se mapean a labels traducidos via helpers (`getStatusBadge`, `getDirectionLabel`, `getVehicleColorLabel`) — la comparación se hace contra el valor original, el render usa `t()`.
- Strings con placeholders en JS usan `.replace('{name}', value)`.
- **Fallback visible**: si una key no existe, `t()` devuelve la key literal — visible en UI para detectar gaps rápido en QA.

## 11. Puntos típicos para mejoras

Áreas donde es probable que se pidan cambios y dónde tocar:

- **Nuevo campo del SDK** → extender extracción en [_process_event](app/anpr_listener.py#L70), incluir en `event_payload`, agregar `ALTER TABLE ADD COLUMN IF NOT EXISTS` en [initialize_database](app/anpr_db_manager.py#L80), INSERT en [insert_anpr_event_db](app/anpr_db_manager.py#L177), filtro en [get_events](app/anpr_db_manager.py#L211), UI en `templates/index.html`.
- **Nueva cámara** → añadir sección `[Camera.X]` con `Id` único en `app/config.ini` y **reconstruir y redesplegar** (`sudo docker-compose build anpr-listener && sudo docker-compose up -d anpr-listener`) — ver sección 12.
- **Notificaciones / alertas por placa** → mencionado en ROADMAP; lugar natural es el listener o un nuevo servicio que consuma `processed_data`.
- **Limpieza de imágenes/logs** → ROADMAP lista log rotation y cleanup; no implementado.
- **WebSockets para dashboard** → ROADMAP; hoy es polling cada N segundos en index.html.
- **Cambios de password policy** → unificar [anpr_web.py:is_password_strong](app/anpr_web.py#L195) con setup.sh.
- **Migración de roles** o nuevos roles → `User.role` en [models.py:11](app/models.py#L11) + checks en decoradores y `api_proxy`.

## 12. Gotchas / cosas a recordar

- `network_mode: host` ⇒ los hostnames de Compose (`mariadb`, `anpr-db-manager`) **no resuelven**. Siempre `localhost`/`127.0.0.1`.
- El directorio `/root/anpr-camera-dev` es propiedad de root y requiere **sudo** para leer/escribir desde un usuario no privilegiado.
- `app/db/` es un bind mount de MariaDB — **no tocar manualmente** con servicios arriba.
- `.dockerignore` excluye `.env` y `setup.sh`, así que no están dentro de las imágenes.
- El listener depende de las `.so` del SDK Dahua, copiadas a `/usr/local/lib/dahua_sdk/` durante el build.
- Cambios en `models.py` o esquema requieren migración manual — no hay Alembic. El patrón actual es `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` en el startup.
- `Confidence` del SDK viene en 0–100 entero; se almacena dividido entre 100. **En cámaras Fase 5 viene 0** — open field investigation, ver `docs/field-investigations.md`.
- El campo `emCarType` se mapea a `access_status` — verificar mapping si Dahua agrega nuevos códigos.
- **`docker restart` NO redeploya código** para listener/db-manager/anpr-web — los `.py` y templates están baked into la imagen. Siempre usar `sudo docker-compose build <svc> && sudo docker-compose up -d <svc>` tras cambios de código. Ver sección 7.
- El campo `camera_id` en `anpr_events` es ahora INT NULL FK → `cameras.id` (no VARCHAR). El campo VARCHAR del nombre se llama `camera_friendly_name`. No confundir los dos.
- El `Id` en `config.ini` debe ser entero. Si falta o es inválido, el listener salta esa cámara con log de error claro — no falla silenciosamente.
- **`app/translations/` se copia al image en `anpr_web.Dockerfile`** (línea `COPY app/translations/ /app/app/translations/`). Si alguien olvida copiarlo en un Dockerfile nuevo, anpr-web fallaría al startup (fail-fast por diseño).
- **i18n con interpolación**: las keys con `{name}`/`{count}`/`{error}` en JSON requieren `.format(name=value)` en Python y `.replace('{name}', value)` en JS — NO interpolación automática.
- **IP de sesión** vive ahora en el dict de session (`session['ip_address']`), NO en `sessions.ip_address` (columna legacy preservada para retrocompat). El admin UI lee el payload decodificado primero, fallback a columna.
- **Estructura de keys i18n**: plana, snake_case, prefijada por área (`header.*`, `filter.*`, `table.col.*`, `status.*`, `direction.*`, `color.*`, `admin.*`, `backend.*`). Mantener orden alfabético dentro del JSON para minimizar diffs.
- **Paridad de keys**: `es.json` y `en.json` deben tener exactamente las mismas keys. Verificar con `diff <(jq -r 'keys[]' es.json | sort) <(jq -r 'keys[]' en.json | sort)` — debe estar vacío.

## 13. Versión actual (de ROADMAP.md)

**v2.6 — i18n ES/EN**: soporte multilenguaje (español + inglés) en toda la UI vía cookie `lang`, 176 keys traducidas en `app/translations/{es,en}.json`, helpers `t()` unificados en Python/Jinja/JS, refactor de flash + jsonify en backend.

Anteriores:
- **v2.5 — Camera Groups + access control**: grupos de cámaras (M:N con cámaras y usuarios), filtro `allowed_camera_ids` en endpoints de lectura, panel admin con tab "Grupos", viewer restringido a cámaras de sus grupos (sin grupos = ve nada).
- **v2.4 — Camera Identity Refactor**: per-camera callback closures, tabla `cameras`, `camera_id` INT FK en `anpr_events`, soporte para múltiples cámaras detrás de IP NAT compartida.
- **v2.3 — Session IP Tracking** (la captura tenía race; arreglada via `session['ip_address']` patch).
- **v2.2 — Auth refinement**: bcrypt, roles admin/viewer, gestión de sesiones, política de password 10+ chars.

Próximos hitos (Near Term en ROADMAP):
- Plate watchlist con notificaciones por Telegram.
- Dashboard WebSocket (reemplazar polling).
- **Phase 6: Tiered image retention** con Backblaze B2 (mes 0-12 local, 13-24 archivado a B2, >24 borrado). Diseño aprobado, implementación pendiente.
- Log rotation para `anpr_listener.log` y `anpr_db_manager.log`.

## 14. Specs, plans y runbooks importantes

Toda la documentación de diseño y operación vive en `docs/`:

- [`docs/superpowers/specs/`](docs/superpowers/specs/) — diseños aprobados por feature.
- [`docs/superpowers/plans/`](docs/superpowers/plans/) — planes operacionales paso a paso (los que ejecutan los subagentes).
- [`docs/deployment/`](docs/deployment/) — runbooks para deploys con migración (ej. camera-groups-migration.md).
- [`docs/field-investigations.md`](docs/field-investigations.md) — anomalías de hardware o configuración detectadas en sitio. Hoy tiene 2 abiertas: asimetría de detección CAM3 vs CAM4, y `confidence=0` en cámaras Fase 5.
