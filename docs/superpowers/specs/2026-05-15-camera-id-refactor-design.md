# Diseño: Refactor de identidad de cámaras (camera_id como FK)

**Fecha**: 2026-05-15
**Autor**: gabriel + Claude (brainstorming)
**Estado**: Aprobado, listo para `writing-plans`

---

## 1. Problema y contexto

El sistema ANPR atribuye eventos de cámaras Dahua a una `FriendlyName` almacenada como `VARCHAR` en `anpr_events.camera_id`. Cuando dos cámaras comparten la misma IP externa vía NAT (port-forwarding con puertos distintos al mismo router/ONU), el listener pierde la capacidad de distinguir cuál cámara originó cada evento.

Causa raíz: el código keyea la identidad por `IPAddress` y por el `attach_id` que devuelve `RealLoadPictureEx` del NetSDK. Bajo NAT compartido, el SDK Dahua colapsa o no respeta esos identificadores. Dos parches previos (mapa IP→tupla y `dwUser` con `id(cam_info)`) no resolvieron el síntoma: los eventos siguen llegando a la última cámara registrada en el config.

**Síntoma observable hoy**: el dashboard muestra todos los eventos de CAM3 y CAM4 etiquetados como "Fase5-Gate-Outside". La placa RLM005 capturada visualmente desde la UI de CAM3 quedó guardada en DB como `camera_id = "Fase5-Gate-Outside"`.

## 2. Objetivos y fuera de alcance

### En alcance (esta iteración)
1. Determinar empíricamente qué mecanismo de atribución funciona en el SDK para CAM3/CAM4.
2. Implementar la atribución correcta en el listener basándose en los datos.
3. Introducir un identificador numérico (`Id`) único por cámara, controlado por nosotros, definido en `config.ini` y proyectado a una nueva tabla `cameras` en MariaDB.
4. Reemplazar `anpr_events.camera_id VARCHAR` por una referencia (`INT FK`) a la nueva tabla, preservando el texto original en `anpr_events.camera_friendly_name`.
5. Backfill de los ~115k eventos históricos: poblar el nuevo `camera_id` (FK) haciendo lookup por `friendly_name` en la tabla `cameras`.
6. Ajustar `db-manager` y la UI web para consumir el nuevo modelo.

### Fuera de alcance (futuro)
- UI de admin para gestionar cámaras (los admins siguen editando `config.ini` y reiniciando).
- Mover la configuración de cámaras a la DB como source-of-truth (decisión deliberada: `config.ini` se mantiene como autoritativo, la tabla `cameras` es un espejo).
- Migración a otra marca de cámaras (el diseño deja el camino abierto pero no se implementa).
- Notificaciones por placa, WebSockets, retención automática.

## 3. Decisión arquitectónica clave

**`config.ini` es el source-of-truth para la configuración de cámaras.**

La tabla `cameras` en MariaDB es un espejo sincronizado al startup del `db-manager`. Editar una cámara = editar `config.ini` + reiniciar el servicio. Esto mantiene la operación familiar para el admin y evita complejidad de bootstrap circular si la DB se cae.

**`anpr_events.camera_id` se convierte en una FK numérica** apuntando a `cameras.id`. La columna VARCHAR original se preserva con un rename a `camera_friendly_name` (denormalizada, legible, sirve como respaldo de la atribución original aunque luego se renombre la cámara).

**El listener pasa a usar el `Id` numérico** (leído de `config.ini`) como identidad primaria. El mecanismo de atribución (closure por cámara vs lookup por `szSerialNo`) se decide en Fase 0 según resultados empíricos.

## 4. Esquema de DB resultante

### Tabla nueva: `cameras`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `INT PRIMARY KEY` | Asignado desde `config.ini` (campo `Id` por sección `[Camera.X]`). No es `AUTO_INCREMENT` — nosotros controlamos los valores. |
| `friendly_name` | `VARCHAR(255) UNIQUE NOT NULL` | Nombre humano-legible mostrado en UI. |
| `ip_address` | `VARCHAR(45)` | IP del config.ini (puede ser pública con NAT). |
| `port` | `INT` | Puerto NetSDK (37777 por defecto). |
| `enabled` | `BOOLEAN DEFAULT TRUE` | Refleja el campo `Enabled` del config.ini. Si una cámara se quita del config, se marca `false` (no se borra, para preservar FK). |
| `created_at` | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | |
| `updated_at` | `TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` | |

### Tabla modificada: `anpr_events`

Cambios en orden:
1. `RENAME COLUMN camera_id TO camera_friendly_name` (preserva los ~115k valores VARCHAR existentes).
2. `ADD COLUMN camera_id INT NULL AFTER plate_number` (nueva FK, nullable durante backfill).
3. Backfill: `UPDATE anpr_events e JOIN cameras c ON c.friendly_name = e.camera_friendly_name SET e.camera_id = c.id`.
4. `ADD INDEX idx_camera_id (camera_id)`.
5. `ADD CONSTRAINT fk_anpr_events_camera FOREIGN KEY (camera_id) REFERENCES cameras(id)`.

`camera_id` queda `NULL` permitido (eventos viejos cuya `friendly_name` no matcheó ninguna cámara actual, p.ej. cámaras descontinuadas, quedan visibles pero sin FK). No eliminamos `camera_friendly_name` — es el respaldo histórico.

## 5. Plan por fases

### Fase 0: Diagnóstico empírico

**Objetivo**: medir cuál de tres mecanismos distingue eventos de CAM3 vs CAM4.

**Cómo**: desplegar una versión instrumentada del listener que, en una sola pasada, prueba ambas hipótesis:
- Registra un callback distinto por cámara (factory closure-based). Cada callback emite un log identificando cuál fue invocado.
- Cada callback adicionalmente loguea a nivel WARNING los campos `lAnalyzerHandle`, `dwUser`, `szSerialNo`, `szSourceID`, `szName`, `nChannelID` del evento.

Esto NO es la implementación final — es solo el diagnóstico. El código vuelve al estado anterior (o avanza a Fase 1 según el resultado) cuando termina la prueba.

**Duración**: ~10 min con tráfico real (o forzando paso de vehículos por las dos cámaras).

**Decisión basada en datos**:
| Observación empírica | Mecanismo elegido para Fase 1 |
|---|---|
| Cada callback recibe solo los eventos de SU cámara | Closure-only |
| Mismo callback recibe ambos eventos, pero `szSerialNo` los distingue | Serial-lookup |
| Ni callback ni serial distinguen | Escalation: 1 cámara por IP pública (limitación de hardware) |

### Fase 1: Arreglo del listener (basado en Fase 0)

**Cambios comunes a cualquier resultado de Fase 0**:
- `config.ini`: agregar campo `Id = <número>` a cada sección `[Camera.X]`.
- Listener valida al startup: `Id` único, `(IPAddress, Port)` único, falla si hay duplicados.
- Estructura `cam_info` incluye `Id`.
- Payload del evento al `db-manager` incluye `CameraId` numérico además del `CameraID` (FriendlyName) que ya manda.
- Filename temporal de imagen usa `cam{Id}` en vez de IP (evita colisión bajo NAT compartido + permite limpieza segura en el `finally`).
- `disconnect_callback` resuelve por `login_id` y loguea con `FriendlyName`.
- Eliminar globals `g_attach_handle_map` y `g_ip_to_friendly_name_map` (innecesarios con la nueva arquitectura).

**Cambio condicional**:
- **Si Fase 0 → closure**: factory `make_analyzer_callback(cam_info)` que retorna un callback distinto por cámara. El callback closes sobre `cam_info` y conoce su identidad sin lookups.
- **Si Fase 0 → serial**: un callback global. Mapa `serial → cam_info` poblado dinámicamente al primer evento de cada cámara (o al startup vía `CLIENT_QueryDeviceInfo` del SDK si está disponible).

### Fase 2: Esquema de DB

**Cambios en `anpr_db_manager.py`, función `initialize_database()`** (sigue el patrón existente de `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`):

1. Crear tabla `cameras` (si no existe) con el esquema de la Sección 4.
2. Verificar si `anpr_events.camera_friendly_name` ya existe; si no, hacer `RENAME COLUMN camera_id TO camera_friendly_name`.
3. `ADD COLUMN IF NOT EXISTS camera_id INT NULL`.
4. Sync de `cameras` desde `config.ini`: UPSERT por `id`. Cámaras removidas de config se marcan `enabled = false`.

Constraint FK + index se agregan después del backfill (Fase 3) para no rechazar la tabla mientras los valores son `NULL`.

### Fase 3: Backfill

1. Verificar que `cameras` tiene filas para todos los `friendly_name` distintos presentes en `anpr_events.camera_friendly_name`. Si falta alguno, ABORTAR (el admin debe agregarlo a config.ini o aceptar que esos eventos queden `NULL`).
2. `UPDATE anpr_events e JOIN cameras c ON c.friendly_name = e.camera_friendly_name SET e.camera_id = c.id`.
3. Reporte: cuántas filas quedaron con `camera_id IS NULL`. Mostrar al admin.
4. Si OK: `ADD INDEX` + `ADD FOREIGN KEY`.

### Fase 4: db-manager y UI web

**`anpr_db_manager.py`**:
- `POST /event`: insertar `camera_id` (numérico, del payload del listener) y `camera_friendly_name` (texto).
- `GET /api/cameras`: cambiar de `SELECT DISTINCT camera_id FROM anpr_events` a `SELECT id, friendly_name, ip_address, port FROM cameras WHERE enabled = TRUE`.
- `GET /api/events`: filtro `camera_id` ahora numérico (FK). Soporte de compatibilidad: si llega string, intentar resolver via `friendly_name`.
- Response de `/api/events`: incluye `camera_id` (int), `camera_friendly_name` (texto). No JOIN por defecto para mantener response liviano.

**`templates/index.html`**:
- Dropdown de filtro: `<option value="${camera.id}">${camera.friendly_name}</option>`.
- Render de fila de tabla: usar `event.camera_friendly_name` en vez de `event.camera_id`.
- `populateCameraFilter()`: ajustar al nuevo shape de la API.

## 6. Plan de rollback

### Respaldos creados
- `/root/anpr-camera-dev/backups/anpr_events_20260515_214304.sql.gz` — dump completo de la DB (5.4 MB).
- `/root/anpr-camera-dev/backups/code_pre_camera_id_refactor_20260515_214324.tar.gz` — archivos del código (23 KB).

### Por fase

| Fase | Si falla | Acción |
|---|---|---|
| 0 | Listener no arranca o no atribuye | `tar xzf` del respaldo → restaurar `anpr_listener.py` → `docker restart anpr-listener`. DB intacta. |
| 1 | Listener crashea o eventos no llegan | Igual que Fase 0 + restaurar `config.ini`. DB intacta. |
| 2 | DDL falla o queda inconsistente | Script SQL de undo preparado: `DROP TABLE cameras`, `ALTER TABLE anpr_events RENAME camera_friendly_name TO camera_id`, `DROP COLUMN camera_id`. Si el undo también falla, `zcat backup.sql.gz \| mysql`. |
| 3 | Backfill produce demasiados NULLs | `UPDATE anpr_events SET camera_id = NULL` deshace solo Fase 3. La columna VARCHAR original (renombrada) sigue intacta. |
| 4 | Dashboard se rompe | Restaurar archivos del tar.gz + restart de containers afectados. Schema queda en estado post-Fase 3 (no se revierte por fallo de UI). |

### Disciplina operacional
- Verificar cada fase antes de avanzar: listener escribe logs, `/health` del db-manager responde 200, dashboard carga.
- Mantener `docker logs -f` de los 3 servicios en una terminal durante la implementación.
- No avanzar a Fase N+1 hasta validar Fase N con eventos reales en vivo (≥1 evento o paso de vehículo).

## 7. Notas para implementación

- Las dos cámaras con NAT compartido (CAM3 en `10.49.9.50:1177`, CAM4 en `10.49.9.50:1277`) son el caso de prueba canónico. Si funciona ahí, funciona en cualquier escenario.
- El SDK Dahua usa codificación `gb2312` para strings — la lectura de `szSerialNo` debe usar ese codec, no UTF-8 (aunque para serials alfanuméricos suelen coincidir).
- `network_mode: host` en docker-compose significa que los servicios se hablan por `localhost`. El listener llama al db-manager en `http://localhost:5001/event`.
- Patrón existente de migración suave: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Usar este mismo patrón para que `initialize_database()` sea idempotente entre restarts.

## 8. Decisiones tomadas durante el brainstorming

- **Alcance**: full (Fase 1+2+backfill+web), descartando solo arreglar el listener.
- **Atribución del evento**: a definir empíricamente en Fase 0. Tres opciones contempladas (closure / serial / escalation).
- **Naming**: `camera_id` se renombra a `camera_friendly_name`. La columna `camera_id` nueva es la FK numérica. Mantiene el nombre que ya usa el frontend (minimiza cambios en index.html).
- **Source-of-truth**: `config.ini` se mantiene canónico. La tabla `cameras` es espejo. (Se consideró migrar todo a DB pero se descartó por familiaridad operacional y para evitar bootstrap circular.)
- **Backfill**: incluido en el alcance. Filas que no matchean cualquier cámara actual quedan con `camera_id NULL` (visible, no rompe nada).
