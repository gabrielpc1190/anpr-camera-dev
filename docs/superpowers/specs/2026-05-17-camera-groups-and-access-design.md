# Diseño: Grupos de cámaras y control de acceso por grupo

**Fecha**: 2026-05-17
**Autor**: gabriel + Claude (brainstorming)
**Estado**: Aprobado, listo para `writing-plans`

---

## 1. Problema y contexto

Hoy el dashboard muestra eventos de **todas** las cámaras a **todos** los usuarios con rol `viewer`. Los 4 viewers actuales (visorjorgecarrillo, visorgilbertvalverde, visorjhonnelizondo, visorbobschlesinger) son cuentas para clientes externos donde cada uno debería ver únicamente las cámaras que le corresponden a su sitio. El sistema actual no permite restringir el acceso a nivel cámara o sitio.

El requerimiento del usuario: *"Necesito poder crear grupos de cámaras, y además asignar permisos a los usuarios para poder ver uno o más grupos."*

## 2. Objetivos y fuera de alcance

### En alcance

1. Modelar **grupos de cámaras** como entidad de primera clase en la DB.
2. Permitir que una cámara pertenezca a múltiples grupos (M:N).
3. Permitir que un viewer tenga acceso a múltiples grupos (M:N).
4. Filtrar `/api/cameras`, `/api/events`, `/api/events/latest_timestamp` y `/images/<filename>` según los grupos del viewer.
5. Panel admin con CRUD de grupos, asignación de cámaras a grupos, asignación de grupos a viewers.
6. Documentar dos escenarios de despliegue (fresh deploy y migración del sistema existente) con respaldos y rollback.

### Fuera de alcance

- Restringir admins por grupo. Los admins siguen viendo todo, sin excepciones (decisión deliberada).
- Permisos granulares dentro de un grupo (ej. "este viewer puede ver eventos del grupo X pero no descargar imágenes"). Acceso es binario por cámara: ve o no ve.
- Grupos jerárquicos (grupos dentro de grupos).
- Migración automatizada de los 4 viewers existentes — el admin lo hace manualmente en la UI nueva después del deploy (ver runbook).

## 3. Decisión arquitectónica clave

**Frontera de autorización: `anpr-web`.** El servicio web (que tiene contexto Flask-Login) computa la lista `allowed_camera_ids` para el viewer actual y la pasa al `anpr-db-manager` como query param. El db-manager filtra al nivel SQL. Esto mantiene db-manager agnóstico de identidad de usuario y centraliza la auth en un solo punto.

**Admins bypass total.** Si `current_user.is_admin`, anpr-web no pasa `allowed_camera_ids`, y db-manager devuelve sin filtro adicional. Garantiza que admins nunca se bloqueen a sí mismos por configuración de grupos.

**Sin grupos = sin acceso (estricto).** Un viewer recién creado sin grupos asignados ve cero cámaras y cero eventos. El admin debe asignarle explícitamente al menos un grupo para que vea algo. Es seguro: imposible filtrar de menos por accidente.

**`initialize_database()` solo crea tablas vacías.** No hace data seeding (no crea grupos placeholder, no asigna viewers automáticamente). La migración de viewers existentes es operacional, manual, vía UI nueva.

## 4. Esquema de DB resultante

### Tablas nuevas

#### `camera_groups`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `INT AUTO_INCREMENT PRIMARY KEY` | id interno |
| `name` | `VARCHAR(255) NOT NULL UNIQUE` | Nombre humano-legible del grupo |
| `description` | `TEXT NULL` | Opcional, para documentación |
| `created_at` | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | |
| `updated_at` | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` | |

#### `camera_group_members` — join cameras ↔ camera_groups

| Columna | Tipo | Notas |
|---|---|---|
| `camera_id` | `INT NOT NULL` | FK → `cameras(id)` `ON DELETE CASCADE` |
| `group_id` | `INT NOT NULL` | FK → `camera_groups(id)` `ON DELETE CASCADE` |
| **PK compuesta** | `(camera_id, group_id)` | Evita duplicados |

#### `user_camera_groups` — join user ↔ camera_groups

| Columna | Tipo | Notas |
|---|---|---|
| `user_id` | `INT NOT NULL` | FK → `user(id)` `ON DELETE CASCADE` |
| `group_id` | `INT NOT NULL` | FK → `camera_groups(id)` `ON DELETE CASCADE` |
| **PK compuesta** | `(user_id, group_id)` | Evita duplicados |

### Tablas existentes — sin cambios

`cameras`, `anpr_events`, `user`, `sessions` no se tocan.

### Query canónica para "cámaras accesibles a un user"

```sql
SELECT DISTINCT cgm.camera_id
FROM camera_group_members cgm
JOIN user_camera_groups ucg ON ucg.group_id = cgm.group_id
WHERE ucg.user_id = <user_id>
```

Para admins: no se ejecuta; se considera "todas".

## 5. Componentes afectados

### 5.1 `app/anpr_db_manager.py`

#### `initialize_database()` — agregar las 3 tablas nuevas

Patrón: `CREATE TABLE IF NOT EXISTS` (idempotente, mismo patrón existente para `cameras` y `anpr_events`). Sin data seeding.

#### `/api/cameras`, `/api/events`, `/api/events/latest_timestamp` — agregar filtro

Cada endpoint acepta un query param opcional `allowed_camera_ids=1,3,7` (comma-separated). Si está presente, se aplica un `WHERE camera_id IN (...)` al SQL. Si está ausente (admin), no se aplica.

Si `allowed_camera_ids` viene vacío (`?allowed_camera_ids=`), tratar como "sin acceso a nada" → devolver lista vacía / 0 eventos. Distinto de no pasarlo (que es "sin filtro").

#### Nuevos endpoints admin

| Endpoint | Método | Acción |
|---|---|---|
| `/api/admin/camera-groups` | `GET` | Lista de grupos con `{id, name, description, camera_count, user_count}` |
| `/api/admin/camera-groups` | `POST` | Crea grupo `{name, description?}` |
| `/api/admin/camera-groups/<id>` | `GET` | Detalle del grupo |
| `/api/admin/camera-groups/<id>` | `PUT` | Actualiza `name`, `description` |
| `/api/admin/camera-groups/<id>` | `DELETE` | Borra (CASCADE limpia memberships) |
| `/api/admin/camera-groups/<id>/cameras` | `GET` | Cámaras en este grupo |
| `/api/admin/camera-groups/<id>/cameras` | `PUT` | Set membership: `{camera_ids: [1,3,5]}` reemplaza la lista completa |
| `/api/admin/users/<id>/camera-groups` | `GET` | Grupos asignados al viewer |
| `/api/admin/users/<id>/camera-groups` | `PUT` | Set assignment: `{group_ids: [1,2]}` reemplaza la lista completa |

Todos detrás de `@admin_required` (decorador ya existente en el código).

### 5.2 `app/anpr_web.py`

#### Helper: `get_allowed_camera_ids(user)`

Función nueva que ejecuta la query canónica vía SQLAlchemy (anpr-web tiene conexión DB para el modelo `User`). Devuelve `list[int]` o `None` para admin.

#### Modificación del proxy `/api/<path>`

Cuando `current_user.is_authenticated and not current_user.is_admin`:
- Calcular `allowed_ids = get_allowed_camera_ids(current_user)`.
- Agregar al query string: `allowed_camera_ids=<ids comma-separated>`.

Cuando admin: pasar el request sin modificar.

#### `/images/<filename>` — validación

Antes de `send_from_directory`:
- Si admin: servir directo (igual que hoy).
- Si viewer: lookup en `anpr_events` por `image_filename = <filename>`, obtener su `camera_id`. Si está en `allowed_camera_ids`, servir. Si no, devolver 403.

Esto requiere una conexión DB en anpr-web (ya existe vía SQLAlchemy).

#### Endpoints admin nuevos

Mirror de los del db-manager pero vía proxy (los del db-manager se llaman desde anpr-web, anpr-web los expone públicamente con `@admin_required`).

### 5.3 `app/templates/admin.html`

- Tercer tab **"Grupos"** junto a Sessions y Users.
- Modal "Crear/Editar grupo" con campo nombre, descripción, y checkboxes con todas las cámaras existentes.
- Extensión del modal "Crear/Editar viewer" (ya existe) con un multi-select de grupos disponibles + warning visual si quedan 0 grupos seleccionados.

### 5.4 `app/templates/index.html` — sin cambios

El dashboard funciona transparentemente con la nueva filtración en backend. `populateCameraFilter()` recibe la lista filtrada vía `/api/cameras`, la tabla muestra lo que devuelve `/api/events`. Ningún cambio de código en el frontend del viewer.

## 6. Plan de rollback

Detallado en el runbook de migración: [`docs/deployment/camera-groups-migration.md`](../../deployment/camera-groups-migration.md).

Resumen de niveles:

| Nivel | Cuándo | Acción |
|---|---|---|
| 1 — UI/endpoint roto | Eventos siguen llegando, viewers no pueden acceder | Restaurar archivos de código desde tar.gz pre-deploy, rebuild containers |
| 2 — Esquema inconsistente | DDL falló a mitad | Nivel 1 + DROP de las 3 tablas nuevas |
| 3 — Catastrófico | Corrupción de datos | Nivel 1 + restore de DB completa desde `mysqldump` gz |

Las nuevas tablas son aditivas (no se modifica ninguna columna ni constraint existente), así que el rollback Nivel 1 deja la DB en estado funcional sin más acción.

## 7. Notas para implementación

- Las queries de `get_allowed_camera_ids(user)` corren en cada request del viewer. Para una performance razonable, agregar índice secundario en `user_camera_groups(user_id)` (la PK compuesta ya cubre user_id como prefix, así que el índice extra no es necesario en MariaDB).
- `camera_group_members(camera_id)` también queda cubierto por el prefix de la PK compuesta. Solo agregar `INDEX (group_id)` si queries por group_id resultan lentas.
- Frontend usa el mismo patrón "Get + checkbox list + Put set-entire-list" que ya tiene el modal de Users — código reutilizable.
- El warning ⚠️ "viewer sin grupos no verá nada" en el modal de viewer es solo visual; no bloquea el guardado (admin puede crear viewer con 0 grupos intencionalmente).
- Patrón existente de migración suave: `CREATE TABLE IF NOT EXISTS`. Usar este mismo patrón en `initialize_database()` para que sea idempotente entre restarts.
- Recordar: archivos Python están baked en las imágenes Docker. Tras cualquier cambio en `anpr_db_manager.py` o `anpr_web.py`, requiere `docker-compose build` + `docker-compose up -d`, no solo `restart`.

## 8. Decisiones tomadas durante el brainstorming

- **Cardinalidad**: M:N en ambos lados (camera↔group y user↔group). Más flexible que 1:N.
- **Alcance de admins**: bypass total. Los grupos solo aplican a viewers.
- **UI**: completa (consistente con el panel actual de Users). No CLI-only ni minimal.
- **Default para viewer sin grupos**: ve nada (estricto). El admin debe asignar explícitamente.
- **Frontera de auth**: anpr-web computa y pasa `allowed_camera_ids` al db-manager. No se duplica lógica de session en db-manager.
- **Data seeding en `initialize_database()`**: ninguno. Las tablas se crean vacías. Migración del sistema existente es operacional, en UI, no automatizada.
- **Escenarios documentados**: dos — fresh deploy (sin migración) y migración del sistema en producción actual (con runbook + respaldos + rollback).
