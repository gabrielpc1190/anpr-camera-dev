# Camera Attribution Fix — Implementation Plan (Fases 0 y 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolver empíricamente el bug de atribución de eventos cuando dos cámaras Dahua comparten una IP externa vía NAT (port-forwarding). Decidir qué mecanismo de identificación funciona con el SDK (closure por cámara vs lookup por szSerialNo) y dejar el listener limpio.

**Architecture:** Una versión instrumentada del listener prueba ambas hipótesis en producción. Los logs revelan qué mecanismo distingue eventos de CAM3 vs CAM4 (ambas en `10.49.9.50` puertos 1177 y 1277). Según los datos, se limpia y se commitea el código final.

**Tech Stack:** Python 3.11, Dahua NetSDK, MariaDB 10.6, Docker Compose, bash. No frameworks de testing automatizado para el listener — la validación es por observación de logs + queries SQL.

**Spec de referencia:** [docs/superpowers/specs/2026-05-15-camera-id-refactor-design.md](../specs/2026-05-15-camera-id-refactor-design.md)

---

## Estado actual del repositorio (importante)

El listener YA tiene aplicado el refactor de "callback por cámara via factory" (closure) y `config.ini` YA tiene `Id = 1/2/3/4`. Pero esto se aplicó **sin validar** justo antes del brainstorming. La DB sigue mostrando eventos mezclados de las pruebas previas. El primer trabajo es decidir si ese refactor funciona o no.

- `/root/anpr-camera-dev/app/anpr_listener.py` — closure refactor live, sin validar (uncommitted)
- `/root/anpr-camera-dev/app/config.ini` — tiene `Id = 1/2/3/4` (uncommitted)
- `/root/anpr-camera-dev/backups/anpr_events_20260515_214304.sql.gz` — DB dump (existe)
- `/root/anpr-camera-dev/backups/code_pre_camera_id_refactor_20260515_214324.tar.gz` — tar de archivos (existe)

---

## Setup compartido (lee esto antes de empezar)

**Permisos**: el código vive en `/root/anpr-camera-dev/` que es propiedad de root. Todos los comandos de lectura/escritura van con `sudo`. Los archivos se editan en `/tmp/` (propiedad del usuario `gabriel`) y se copian con `sudo cp`.

**Patrón de edición**:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_listener.py /tmp/anpr_listener.py
sudo chown gabriel:gabriel /tmp/anpr_listener.py
# Editar /tmp/anpr_listener.py con Edit tool
python3 -m py_compile /tmp/anpr_listener.py    # validar sintaxis
sudo cp /tmp/anpr_listener.py /root/anpr-camera-dev/app/anpr_listener.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_listener.py
sudo docker restart anpr-listener
```

**Variables que usaremos**:
```bash
LISTENER_PATH=/root/anpr-camera-dev/app/anpr_listener.py
LOG_PATH=/root/anpr-camera-dev/app/logs/anpr_listener.log
ENV_FILE=/root/anpr-camera-dev/.env
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" "$ENV_FILE" | cut -d= -f2)
```

**Convención de tiempo**: la DB almacena timestamps de evento en la zona horaria del evento de la cámara (no UTC necesariamente). El campo `created_at` es UTC del momento del INSERT. Usar `created_at` para filtrar por "después del restart".

---

## Fase 0 — Diagnóstico empírico

### Task 1: Pre-flight check (verificar baseline)

**Files:** ninguno (solo lectura/verificación)

- [ ] **Step 1.1: Verificar que los respaldos existen**

Run:
```bash
sudo ls -lah /root/anpr-camera-dev/backups/anpr_events_20260515_214304.sql.gz \
              /root/anpr-camera-dev/backups/code_pre_camera_id_refactor_20260515_214324.tar.gz
```

Expected: ambos archivos listados con tamaño > 0.

- [ ] **Step 1.2: Verificar que el listener está corriendo**

Run:
```bash
sudo docker ps --filter name=anpr-listener --format "{{.Names}}: {{.Status}}"
```

Expected: una línea como `anpr-listener: Up X minutes/hours`.

- [ ] **Step 1.3: Capturar el ID del último evento Fase5 en DB como marca de baseline**

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -Nse \
  "SELECT MAX(id) FROM anpr_events WHERE camera_id LIKE 'Fase5%';" 2>/dev/null
```

Expected: un número entero (el last ID Fase5 antes de empezar). Guardar esto como `BASELINE_LAST_ID` para comparar después.

- [ ] **Step 1.4: Verificar conectividad TCP a las cámaras**

Run:
```bash
for HOSTPORT in 10.45.14.11:37777 10.45.14.12:37777 10.49.9.50:1177 10.49.9.50:1277; do
  timeout 3 nc -vz ${HOSTPORT%:*} ${HOSTPORT#*:} 2>&1 | tail -1
done
```

Expected: las 4 líneas deben mostrar `open`. Si alguna falla, parar y diagnosticar antes de seguir.

### Task 2: Instrumentar el listener (Fase 0 DIAG)

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_listener.py` (líneas ~65-160 — función `_process_event` y `make_analyzer_callback`)

- [ ] **Step 2.1: Sacar el archivo actual a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_listener.py /tmp/anpr_listener.py
sudo chown gabriel:gabriel /tmp/anpr_listener.py
md5sum /tmp/anpr_listener.py
```

Expected: el comando devuelve el md5 sin error. Apuntar el md5 para verificación posterior.

- [ ] **Step 2.2: Agregar logging diagnóstico al inicio de `_process_event`**

Editar `/tmp/anpr_listener.py`. Localizar la función `_process_event` (alrededor de línea 70). Insertar inmediatamente después de `camera_friendly_name = cam_info["FriendlyName"]` (antes de `image_filepath = None`):

Código a insertar:
```python
    # --- DIAG FASE 0: identificar mecanismos del SDK ---
    try:
        _serial = bytes(alarm_info.szSerialNo).rstrip(b'\x00').decode('gb2312', 'ignore').strip()
        _source = bytes(alarm_info.szSourceID).rstrip(b'\x00').decode('gb2312', 'ignore').strip()
        _name = bytes(alarm_info.szName).rstrip(b'\x00').decode('gb2312', 'ignore').strip()
        _channel = alarm_info.nChannelID
        logger.warning(
            f"DIAG ev closure_for=Id{camera_id}({camera_friendly_name}) "
            f"nChannelID={_channel} szSerialNo='{_serial}' "
            f"szSourceID='{_source}' szName='{_name}'"
        )
    except Exception as _e:
        logger.warning(f"DIAG ev: failed to read id fields: {_e}")
    # --- FIN DIAG ---
```

Razón: este log nos dice **qué closure se invocó** (lo determina `camera_id` capturado por closure) Y **qué campos vienen en el payload** del evento (que permitirían distinguir cámaras sin closure). Comparando los logs de varios eventos, podemos saber si closures funcionan y/o si szSerialNo es útil.

- [ ] **Step 2.3: Agregar logging diagnóstico al éxito de subscription en `connect_camera`**

Editar `/tmp/anpr_listener.py`. Localizar `connect_camera` (alrededor de línea 166). Después de la línea `logger.info(f"Subscription SUCCESS: {cam_info['FriendlyName']} (attach_id={attach_id})")`, agregar:

```python
                logger.warning(
                    f"DIAG sub: Id{cam_info['Id']} ({cam_info['FriendlyName']}) "
                    f"login_id={login_id} attach_id={attach_id} "
                    f"callback_id={id(cam_info['callback'])}"
                )
```

Razón: nos dice si dos cámaras reciben el **mismo `attach_id`** del SDK (evidencia de coalescing) y confirma que cada cámara tiene un objeto callback Python distinto (id() distinto).

- [ ] **Step 2.4: Validar sintaxis Python**

Run:
```bash
python3 -m py_compile /tmp/anpr_listener.py && echo "Sintaxis OK"
```

Expected: imprime `Sintaxis OK`. Si falla, revisar la edición — el `try/except` debe estar a 4 espacios de indent (dentro de la función), no a 0.

- [ ] **Step 2.5: Desplegar al contenedor + reiniciar**

Run:
```bash
sudo cp /tmp/anpr_listener.py /root/anpr-camera-dev/app/anpr_listener.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_listener.py
sudo docker restart anpr-listener
```

Expected: `anpr-listener` impreso (confirmación del restart).

- [ ] **Step 2.6: Esperar a que termine el login de todas las cámaras**

Run:
```bash
until sudo grep -q "Subscription SUCCESS.*Fase5-Gate-Outside" /root/anpr-camera-dev/app/logs/anpr_listener.log \
   || [ $(($(date +%s) - START)) -gt 60 ]; do
  START=${START:-$(date +%s)}; sleep 2;
done
echo "Done waiting"
sudo grep "DIAG sub" /root/anpr-camera-dev/app/logs/anpr_listener.log | tail -10
```

Expected: 4 líneas `DIAG sub`, una por cámara. Cada línea muestra `login_id`, `attach_id`, `callback_id`. **Si dos cámaras (Id3, Id4) muestran el mismo `attach_id` → primera evidencia de coalescing del SDK. Si tienen `callback_id` distintos → confirmamos que registramos callbacks diferentes.**

- [ ] **Step 2.7: Verificar que no haya errores de login**

Run:
```bash
sudo docker logs --since=2m anpr-listener 2>&1 | grep -E "FAILED|ERROR|帐号|主连接" | tail
```

Expected: vacío. Si aparece algún error, parar y diagnosticar antes de continuar.

### Task 3: Capturar datos empíricos (evento por cámara)

Esta tarea necesita coordinación con el operador (usuario) para forzar tráfico controlado. NO automatizable.

- [ ] **Step 3.1: Pedirle al operador que pase un vehículo frente a CAM3 (Fase5-Gate-Inside) y NADA frente a CAM4**

Mientras esto sucede, observar en tiempo real:
```bash
sudo tail -F /root/anpr-camera-dev/app/logs/anpr_listener.log | grep DIAG
```

Expected: una o más líneas `DIAG ev closure_for=Id3(Fase5-Gate-Inside) ...` — la closure de CAM3 fue invocada.

**Si dice `closure_for=Id4(Fase5-Gate-Outside)` aunque el vehículo pasó por CAM3** → closures NO funcionan, el SDK invocó el callback "equivocado". Pasar a Fase 1B.

**Si dice `closure_for=Id3(Fase5-Gate-Inside)` correctamente** → closures funcionan. Verificar también que `szSerialNo` viene poblado (sirve como Plan B futuro).

- [ ] **Step 3.2: Pedirle al operador que pase un vehículo frente a CAM4 (Fase5-Gate-Outside) y NADA frente a CAM3**

Observar:
```bash
sudo tail -F /root/anpr-camera-dev/app/logs/anpr_listener.log | grep DIAG
```

Expected (si closures funcionan): líneas `DIAG ev closure_for=Id4(Fase5-Gate-Outside) ...`.

- [ ] **Step 3.3: Capturar el snippet de logs para análisis**

Run:
```bash
sudo grep "DIAG" /root/anpr-camera-dev/app/logs/anpr_listener.log | tail -30 | tee /tmp/diag_capture.txt
```

Expected: 4 líneas `DIAG sub` + N líneas `DIAG ev`. Guardar el archivo `/tmp/diag_capture.txt` como evidencia para el análisis.

- [ ] **Step 3.4: Verificar en la DB qué etiquetas recibieron los eventos**

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SELECT id, plate_number, camera_id,
          JSON_EXTRACT(processed_data, '\$.CameraId') AS internal_id,
          timestamp, created_at
   FROM anpr_events
   WHERE id > ${BASELINE_LAST_ID:-0}
     AND camera_id LIKE 'Fase5%'
   ORDER BY id;" 2>&1 | grep -v "Using a password"
```

(Sustituir `${BASELINE_LAST_ID}` con el valor capturado en Step 1.3.)

Expected:
- Si closures funcionan: eventos de CAM3 tienen `camera_id = "Fase5-Gate-Inside"` e `internal_id = 3`. Eventos de CAM4 tienen `camera_id = "Fase5-Gate-Outside"` e `internal_id = 4`.
- Si no funcionan: ambos quedan con la misma etiqueta (la última cámara registrada).

### Task 4: Decisión y limpieza diagnóstica

- [ ] **Step 4.1: Analizar los datos capturados**

Inspeccionar `/tmp/diag_capture.txt` y la salida de la query DB. Comparar:

| Evidencia esperada para "closures funcionan" | Evidencia esperada para "closures NO funcionan" |
|---|---|
| `DIAG ev closure_for=Id3(...)` SOLO cuando pasó por CAM3 | `DIAG ev closure_for=Id4(...)` aparece para todos los eventos sin importar la cámara |
| En DB: `camera_id` distinto entre eventos CAM3 y CAM4 | En DB: todos los eventos tienen el mismo `camera_id` |
| `DIAG sub` muestra `attach_id` distinto por cámara | `DIAG sub` muestra `attach_id` igual (o muy similar) para CAM3 y CAM4 |
| `szSerialNo` poblado y distinto por cámara (bonus: Plan B viable) | `szSerialNo` igual o vacío |

Anotar resultado: **"closures funcionan"** o **"closures NO funcionan, pero szSerialNo distingue"** o **"ni closures ni serial distinguen"**.

- [ ] **Step 4.2: Decidir camino para Fase 1**

| Resultado de Step 4.1 | Camino |
|---|---|
| Closures funcionan | Saltar a Task 5A (Fase 1A: limpiar diagnóstico, dejar closure como solución) |
| Closures fallan, serial distingue | Ir a Task 5B (Fase 1B: refactor a serial-lookup) |
| Ni closures ni serial distinguen | Detener implementación. Reportar al usuario que es limitación física del SDK Dahua → la solución es 1 cámara por IP pública (escalation operacional, no de software). |

---

## Fase 1A — Limpieza y commit (si closures funcionan)

### Task 5A: Remover el diagnóstico DIAG

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_listener.py`

- [ ] **Step 5A.1: Sacar el archivo actual a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_listener.py /tmp/anpr_listener.py
sudo chown gabriel:gabriel /tmp/anpr_listener.py
```

- [ ] **Step 5A.2: Remover el bloque DIAG ev de `_process_event`**

Editar `/tmp/anpr_listener.py`. Buscar el bloque que empieza con `# --- DIAG FASE 0:` y termina con `# --- FIN DIAG ---`. Borrar todo el bloque incluyendo las dos líneas de delimitadores.

- [ ] **Step 5A.3: Remover la línea DIAG sub de `connect_camera`**

Buscar el bloque `logger.warning(f"DIAG sub: Id{cam_info['Id']} ...")` (3 líneas). Borrar todo el bloque.

- [ ] **Step 5A.4: Validar sintaxis**

Run:
```bash
python3 -m py_compile /tmp/anpr_listener.py && echo "Sintaxis OK"
```

Expected: `Sintaxis OK`.

- [ ] **Step 5A.5: Verificar que no quedan referencias huérfanas a DIAG**

Run:
```bash
grep -n "DIAG" /tmp/anpr_listener.py
```

Expected: vacío (no debe quedar nada con "DIAG").

- [ ] **Step 5A.6: Desplegar y reiniciar**

Run:
```bash
sudo cp /tmp/anpr_listener.py /root/anpr-camera-dev/app/anpr_listener.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_listener.py
sudo docker restart anpr-listener
```

Expected: `anpr-listener` impreso.

- [ ] **Step 5A.7: Verificar arranque limpio**

Run:
```bash
sleep 15
sudo docker logs --since=20s anpr-listener 2>&1 | grep -E "FAILED|ERROR|帐号|主连接" | tail
```

Expected: vacío.

### Task 6A: Validación end-to-end de Fase 1A

- [ ] **Step 6A.1: Confirmar que cámaras 1 y 2 siguen registrando eventos**

Esperar a que pase tráfico natural por CAM1 o CAM2 (no se requiere acción del operador si hay tráfico). Después de unos minutos:

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SELECT camera_id, COUNT(*) AS cnt, MAX(created_at) AS last
   FROM anpr_events
   WHERE created_at >= NOW() - INTERVAL 10 MINUTE
   GROUP BY camera_id;" 2>&1 | grep -v "Using a password"
```

Expected: rows para `Cinco Ventanas` y `Las Brisas` con `cnt > 0`. Si una de las dos no tiene eventos en 10 min, puede ser falta de tráfico (no necesariamente bug); verificar con operador.

- [ ] **Step 6A.2: Forzar evento por CAM3 y verificar atribución correcta**

Operador pasa vehículo por CAM3. Esperar 30s y:

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SELECT id, plate_number, camera_id, timestamp
   FROM anpr_events
   WHERE camera_id = 'Fase5-Gate-Inside'
   ORDER BY id DESC LIMIT 3;" 2>&1 | grep -v "Using a password"
```

Expected: al menos 1 row reciente con `camera_id = "Fase5-Gate-Inside"`. Si aparece `Fase5-Gate-Outside`, Fase 1A FALLÓ — la closure no estaba realmente funcionando, regresar a Step 4.2 con datos nuevos.

- [ ] **Step 6A.3: Forzar evento por CAM4 y verificar atribución correcta**

Operador pasa vehículo por CAM4. Esperar 30s y:

Run:
```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SELECT id, plate_number, camera_id, timestamp
   FROM anpr_events
   WHERE camera_id = 'Fase5-Gate-Outside'
     AND created_at >= NOW() - INTERVAL 5 MINUTE
   ORDER BY id DESC LIMIT 3;" 2>&1 | grep -v "Using a password"
```

Expected: al menos 1 row reciente con `camera_id = "Fase5-Gate-Outside"`.

### Task 7A: Commit a git

- [ ] **Step 7A.1: Revisar el diff vs HEAD para confirmar que solo está lo que queremos**

Run:
```bash
sudo git -C /root/anpr-camera-dev diff HEAD -- app/anpr_listener.py app/config.ini | head -80
```

Expected: el diff muestra los cambios del refactor closure + `Id` en config. Si aparece algo de DIAG, regresar a Task 5A.

- [ ] **Step 7A.2: Stage los archivos del refactor**

Run:
```bash
sudo git -C /root/anpr-camera-dev add app/anpr_listener.py app/config.ini
sudo git -C /root/anpr-camera-dev status --short
```

Expected: `M app/anpr_listener.py` y `M app/config.ini` (en mayúscula = staged).

- [ ] **Step 7A.3: Crear el commit con HEREDOC**

Run:
```bash
sudo git -C /root/anpr-camera-dev commit -m "$(cat <<'EOF'
fix(listener): attribute events to correct camera under shared-IP NAT

Refactor analyzer callback registration to use a per-camera closure
factory (make_analyzer_callback). Each camera now has its own callback
function pointer that captures cam_info via Python closure, so SDK
events are routed by the SDK's per-subscription callback dispatch
instead of by handle/dwUser values that the NetSDK does not respect
when multiple cameras share an external IP via port forwarding.

Also adds an Id field per [Camera.X] section in config.ini (our
internal unique identifier, not the SDK's), which is now included in
the event payload as CameraId. The legacy CameraID (FriendlyName) is
preserved for db-manager backward compatibility.

Removed globals g_attach_handle_map and g_ip_to_friendly_name_map
that depended on IP uniqueness.

Verified empirically on cameras 10.49.9.50:1177 (Id=3) and
10.49.9.50:1277 (Id=4): each camera's events now arrive at its own
closure callback, breaking the prior coalescing behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit hash impreso, sin errores.

- [ ] **Step 7A.4: Verificar el commit**

Run:
```bash
sudo git -C /root/anpr-camera-dev log -1 --stat
```

Expected: el commit con los 2 archivos listados.

---

## Fase 1B — Refactor a serial-lookup (si closures NO funcionan)

Esta sección solo se ejecuta si Step 4.2 decidió `closures fallan, serial distingue`.

### Task 5B: Reemplazar callback factory con callback global + serial-lookup

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_listener.py`

- [ ] **Step 5B.1: Sacar el archivo actual a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_listener.py /tmp/anpr_listener.py
sudo chown gabriel:gabriel /tmp/anpr_listener.py
```

- [ ] **Step 5B.2: Reemplazar `make_analyzer_callback` con un callback global + mapa por serial**

Editar `/tmp/anpr_listener.py`. Buscar la función `make_analyzer_callback` (alrededor de línea 147) y reemplazarla completa por:

```python
# Mapa serial → cam_info, poblado dinámicamente al primer evento de cada cámara.
# Si szSerialNo viene vacío o no matchea ninguna cámara configurada, el evento se descarta.
g_serial_to_cam = {}

@CB_FUNCTYPE(None, C_LLONG, C_DWORD, c_void_p, POINTER(c_ubyte), C_DWORD, C_LDWORD, c_int, c_void_p)
def analyzer_data_callback(lAnalyzerHandle, dwAlarmType, pAlarmInfo, pBuffer, dwBufSize, dwUser, nSequence, reserved):
    if dwAlarmType != EM_EVENT_IVS_TYPE.TRAFFICJUNCTION:
        return
    alarm_info = cast(pAlarmInfo, POINTER(DEV_EVENT_TRAFFICJUNCTION_INFO)).contents
    try:
        serial = bytes(alarm_info.szSerialNo).rstrip(b'\x00').decode('gb2312', 'ignore').strip()
    except Exception as e:
        logger.error(f"No se pudo leer szSerialNo del evento: {e}")
        return
    if not serial:
        logger.warning("Evento sin szSerialNo — descartado (no se puede atribuir).")
        return
    cam_info = g_serial_to_cam.get(serial)
    if cam_info is None:
        # Resolución perezosa: buscar la cámara cuyo IPAddress matchea el ip del SDK del evento.
        # Fallback más débil; idealmente se popula al login pero requiere CLIENT_QueryDevInfo.
        logger.warning(f"Serial '{serial}' no asociado a ninguna cámara. Eventos descartados hasta que el operador haga el binding.")
        return
    _process_event(cam_info, alarm_info, pBuffer, dwBufSize)
```

- [ ] **Step 5B.3: Cambiar `connect_camera` para registrar el callback global y mapear serial**

Editar `/tmp/anpr_listener.py`. Buscar en `connect_camera` donde dice `if cam_info.get("callback") is None: cam_info["callback"] = make_analyzer_callback(cam_info)`. Reemplazar ese bloque por:

```python
            # En modo serial-lookup, todas las cámaras comparten un callback global.
            # El binding serial→cam_info se hace al primer evento o se podría hacer aquí
            # via sdk.QueryDevInfo si el SDK lo expone (no implementado en esta iteración).
```

Y el siguiente `attach_id = sdk.RealLoadPictureEx(login_id, 0, ..., cam_info["callback"], 0, None)`:

```python
            attach_id = sdk.RealLoadPictureEx(login_id, 0, EM_EVENT_IVS_TYPE.TRAFFICJUNCTION, 1, analyzer_data_callback, 0, None)
```

(Reemplaza `cam_info["callback"]` por `analyzer_data_callback` — el callback global.)

- [ ] **Step 5B.4: Validar sintaxis**

Run:
```bash
python3 -m py_compile /tmp/anpr_listener.py && echo "Sintaxis OK"
```

Expected: `Sintaxis OK`.

- [ ] **Step 5B.5: Desplegar y reiniciar**

Run:
```bash
sudo cp /tmp/anpr_listener.py /root/anpr-camera-dev/app/anpr_listener.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_listener.py
sudo docker restart anpr-listener
sleep 15
sudo docker logs --since=20s anpr-listener 2>&1 | grep -E "FAILED|ERROR|no asociado|帐号|主连接" | tail
```

Expected: sin errores de login. Cuando el operador pase un vehículo, el log mostrará `Serial '<xxx>' no asociado` (esperado en este punto — todavía no hicimos bootstrap del mapeo).

### Task 6B: Bootstrap del mapa serial → Id

- [ ] **Step 6B.1: Capturar los serials reales de cada cámara**

Operador pasa vehículo por cada cámara (orden libre). Mientras tanto:

Run:
```bash
sudo tail -F /root/anpr-camera-dev/app/logs/anpr_listener.log | grep "no asociado"
```

Expected: 1 línea por cámara, mostrando el serial. Apuntar los 4 serials.

- [ ] **Step 6B.2: Precargar `g_serial_to_cam` en el listener**

Editar `/tmp/anpr_listener.py`. Buscar el loop al final de `main()` donde se hace `for cam in CONFIGURED_CAMERAS: connect_camera(cam)`. INMEDIATAMENTE antes de ese loop, agregar:

```python
    # Bootstrap del mapeo serial → cam_info. Los serials se obtienen pasando
    # un vehículo por cada cámara una vez y leyendo el log.
    SERIAL_TO_ID = {
        "SERIAL_CAM1": 1,   # Cinco Ventanas (reemplazar con serial real)
        "SERIAL_CAM2": 2,   # Las Brisas
        "SERIAL_CAM3": 3,   # Fase5-Gate-Inside
        "SERIAL_CAM4": 4,   # Fase5-Gate-Outside
    }
    for cam in CONFIGURED_CAMERAS:
        for serial, want_id in SERIAL_TO_ID.items():
            if cam["Id"] == want_id:
                g_serial_to_cam[serial] = cam
                break
    logger.info(f"Pre-cargados {len(g_serial_to_cam)} mapeos serial→cam_info")
```

Reemplazar `SERIAL_CAM1`...`SERIAL_CAM4` con los valores reales del Step 6B.1.

- [ ] **Step 6B.3: Validar, desplegar, reiniciar**

Run:
```bash
python3 -m py_compile /tmp/anpr_listener.py && echo "Sintaxis OK"
sudo cp /tmp/anpr_listener.py /root/anpr-camera-dev/app/anpr_listener.py
sudo chown root:root /root/anpr-camera-dev/app/anpr_listener.py
sudo docker restart anpr-listener
sleep 15
sudo grep "Pre-cargados" /root/anpr-camera-dev/app/logs/anpr_listener.log | tail
```

Expected: `Pre-cargados 4 mapeos serial→cam_info`.

- [ ] **Step 6B.4: Validar end-to-end como en Task 6A**

Repetir Steps 6A.1, 6A.2, 6A.3 (validación de cámaras 1, 2, 3, 4) — la lógica de verificación es la misma.

### Task 7B: Commit a git

- [ ] **Step 7B.1: Revisar diff vs HEAD**

Run:
```bash
sudo git -C /root/anpr-camera-dev diff HEAD -- app/anpr_listener.py app/config.ini | head -100
```

Expected: cambios coherentes con serial-lookup.

- [ ] **Step 7B.2: Stage y commit**

Run:
```bash
sudo git -C /root/anpr-camera-dev add app/anpr_listener.py app/config.ini
sudo git -C /root/anpr-camera-dev commit -m "$(cat <<'EOF'
fix(listener): attribute events via SDK szSerialNo lookup

The Dahua NetSDK coalesces analyzer subscriptions when cameras share
an external IP, so per-subscription callbacks all receive events
through the same handle. Per-camera closure callbacks were tested
empirically and did not distinguish events.

Switched to a single global callback that reads szSerialNo from the
event payload and looks up the cam_info in g_serial_to_cam, which is
pre-populated at startup with serials captured during initial setup.

The Id field in config.ini remains the internal identifier sent in
the event payload as CameraId.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Fase 0/1 — Rollback

Si cualquier paso de Task 2-7 falla y queremos volver al estado pre-experimentos:

```bash
# Restaurar archivos desde el respaldo
sudo tar xzf /root/anpr-camera-dev/backups/code_pre_camera_id_refactor_20260515_214324.tar.gz \
  -C /tmp/restore/ && \
sudo cp -r /tmp/restore/app/* /root/anpr-camera-dev/app/

# Reiniciar
sudo docker restart anpr-listener
```

La DB no se toca en Fases 0 y 1 — sigue intacta.

---

## Self-review check

Antes de empezar la implementación, verificar:

- [ ] Spec coverage: cada requirement del spec (Sec 5 Fase 0 y Fase 1) tiene una task asignada arriba. Sí: Task 2-4 cubre Fase 0; Task 5A/B + 6A/B cubre Fase 1.
- [ ] Placeholder scan: no hay `TBD`, `TODO`, `implement later` en el plan.
- [ ] Type consistency: `cam_info["Id"]` y `camera_id` (variable local) vs `event.camera_id` (campo DB) — son cosas distintas, no se confunden en el código.
- [ ] Rollback claro: sí, sección dedicada.
- [ ] Bites de 2-5 min: cada step es atómico.
