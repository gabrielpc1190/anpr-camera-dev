# Compresión JXL — Diseño

**Fecha:** 2026-06-05
**Estado:** Aprobado por el usuario; pendiente de plan de implementación.
**Tipo:** Feature operativa (ahorro de almacenamiento)

## Resumen

Reducir el almacenamiento del corpus de imágenes ANPR mediante transcodificación
lossless a JPEG XL. Conversión bit-exact reversible, ahorro empírico medido del
17% sobre el corpus existente (~20 GB sobre 119 GB), cero impacto en el
frontend (las imágenes se sirven como JPG vía decodificación on-the-fly desde el
backend). Entrega en dos fases para priorizar el alivio de disco urgente.

## Motivación

- **Disco al 73 % post-limpieza inicial** (52 GB libres de 196 GB).
- **Crecimiento ~745 MB/día** = ~22 GB/mes. Sin acción, margen crítico (~15 GB
  libres) llega en ~6 semanas.
- **Benchmark empírico** en `/tmp/anpr-compression-test/` validó JXL lossless
  como ganador: 17.0 % de ahorro, 100/100 reconstrucciones bit-exact, ~5.4
  img/s single-threaded. Otros formatos (AVIF, WebP, jpegli q85+) o no ahorran
  o introducen pérdida.
- **Cámaras Dahua emiten JPEG con Q=71** (medido). Re-comprimir a Q alto no
  ahorra — lo único viable sobre el JPEG ya optimizado es la recodificación
  lossless de coeficientes que ofrece JXL.

## Decisiones de diseño

1. **JXL lossless de JPEG** (`cjxl -d 0 -j 1`) como formato único. Reversible
   bit-exact con `djxl`, sin riesgo legal/evidencia.
2. **Backend traduce al servir.** El frontend nunca ve `.jxl`. Pide `foo.jpg`
   como hoy; si en disco hay `foo.jxl`, el backend decodifica y devuelve bytes
   JPG.
3. **Nombre base sin cambios.** La tabla `events` sigue almacenando
   `foo.jpg` aunque en disco haya `foo.jxl`. No se migra la DB.
4. **Toggles independientes** para corpus histórico (CLI manual) y para ingesta
   nueva (Settings UI). El primero ya está en producción como script; el
   segundo se construye en Fase 2.
5. **Cache LRU en backend: documentado, NO implementado en v1.** La cantidad
   actual de viewers es baja; se reevalúa si se observa latencia.
6. **Watcher async via inotify** para ingesta nueva, en lugar de inline-sync.
   Cero impacto en el listener Dahua aunque haya picos de eventos.

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Frontend (browser)                          │
│  Pide foo.jpg como siempre  ◄────────────────────┐                  │
└─────────────────────────────────────────────────────────────────────┘
                                                    │
┌────────────────────────────┐                     │
│ A. Backend anpr-web        │  serve_image():     │
│  - busca foo.jpg en disco  │  ┌────────────────┐ │
│  - si no, busca foo.jxl    │──┤ djxl decode    ├─┘
│  - decodifica con djxl     │  │ → bytes JPG    │
│  - devuelve bytes JPG      │  └────────────────┘
└────────────────────────────┘
              ▲
              │ lee de
              │
       ┌──────┴──────────────┐
       │ /app/anpr_images/    │
       │   mix de .jpg y .jxl │
       └──────┬──────────────┘
              ▲
              │ escribe / reemplaza
              │
┌─────────────┴───────────────┐        ┌────────────────────────────┐
│ D. CLI batch (ya hecho)     │        │ C. Watcher async (Fase 2)  │
│  scripts/transcode_jxl.py   │        │  - inotify IN_CLOSE_WRITE  │
│  - corpus histórico         │        │  - lee B (toggle DB)       │
│  - one-shot, foreground     │        │  - pool de 4 workers       │
└─────────────────────────────┘        │  - mismo módulo transcode  │
                                       └─────────────┬──────────────┘
                                                     │ lee
                                                     ▼
                                       ┌────────────────────────────┐
                                       │ B. Toggle (Fase 2)         │
                                       │  app_settings table        │
                                       │  compress_new_images bool  │
                                       │  Admin UI tab "Config"     │
                                       └────────────────────────────┘
```

## Componentes

### A. Backend transparente al formato (Fase 1)

**Archivo:** `app/anpr_web.py` y eventuales endpoints similares en
`app/anpr_db_manager.py` que sirven imágenes.

**Lógica de `serve_image()`:**

1. Cliente pide `/anpr_images/<nombre>.jpg`.
2. Verificar acceso (control de grupos camera, ya existente).
3. `path_jpg = /app/anpr_images/<nombre>.jpg`.
4. Si `path_jpg` existe → `send_file(path_jpg, mimetype='image/jpeg')`.
5. Si no existe, `path_jxl = path_jpg.with_suffix('.jxl')`.
6. Si `path_jxl` existe:
   - Spawn `djxl <path_jxl> -` con `subprocess.run(..., capture_output=True, timeout=5)`.
   - En return-code 0 y stdout no vacío:
     `return Response(stdout, mimetype='image/jpeg', headers=cache_headers)`.
   - En cualquier fallo o timeout: loggear y devolver 404.
7. Si ninguno existe → 404.

**Trade-offs aceptados:**
- ~50 ms extra por imagen decodificada (medido en benchmark).
- Sin cache LRU. Si la UX se siente lenta cuando haya más viewers, ver "Trabajo
  futuro" más abajo.
- `djxl` se invoca como subproceso (más simple y portable que bindings nativos
  de libjxl; el costo de fork es < 5 ms en Linux).

### B. Toggle de configuración en admin (Fase 2)

**Schema nuevo:**

```sql
CREATE TABLE IF NOT EXISTS app_settings (
  setting_key   VARCHAR(64) PRIMARY KEY,
  setting_value VARCHAR(255) NOT NULL,
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  updated_by    INT NULL
);

INSERT IGNORE INTO app_settings (setting_key, setting_value)
VALUES ('compress_new_images', 'false');
```

**Endpoints nuevos en `anpr_db_manager.py`:**

- `GET /api/admin/settings` → dict con todas las settings. Admin-only.
- `PUT /api/admin/settings/<key>` → body `{"value": "..."}`. Admin-only,
  validación de keys conocidas (lista blanca).

Espejo en `anpr_web.py` para que el admin panel pueda llamar el endpoint
(siguiendo el patrón de los 9 endpoints de `camera-groups` mirrored ya
existentes).

**UI:**

Nuevo tab "Configuración" en `templates/admin.html` (siguiendo el mismo patrón
camelCase de los tabs existentes: `tabConfig` / `panelConfig`). Contiene un
checkbox por setting:

- `[x] Comprimir imágenes nuevas automáticamente`
  Tooltip: "Ahorra aprox. 17% en disco. La compresión es lossless y reversible.
  El procesamiento tarda unos segundos por imagen y corre en segundo plano."

Las strings se agregan a `app/translations/es.json` y `en.json` con prefijo
`admin.settings.*`.

### C. Watcher async via inotify (Fase 2)

**Servicio Docker nuevo:** `anpr-jxl-watcher`.

**Stack:**
- Python 3.11.
- Librería `inotify_simple` (sin dependencias C extras).
- Módulo compartido `app/jxl_transcode.py` extraído del CLI de Fase 1 (mismo
  algoritmo: cjxl, verify bit-exact, replace).
- Pool de 4 threads concurrentes (`concurrent.futures.ThreadPoolExecutor`).
  `cjxl` libera el GIL durante el subprocess, así que threads son suficientes;
  no hace falta multiprocess.

**Archivos nuevos:**
- `app/anpr_jxl_watcher.py` — main loop.
- `app/jxl_transcode.py` — módulo refactorizado desde el script CLI.
- `anpr_jxl_watcher.Dockerfile` — imagen del watcher.

**Comportamiento:**
1. Al startup, sweep inicial: para cada `*.jpg` sin `*.jxl` pareja, encolar.
   Cubre el caso de eventos perdidos durante restart.
2. Subscribir inotify a `/app/anpr_images/` con `IN_CLOSE_WRITE`.
3. Para cada evento `.jpg`:
   - Consultar `compress_new_images` en DB (con conexión cached, refresh cada
     30 s, no por evento).
   - Si toggle off: descartar.
   - Si toggle on: encolar al pool.
4. Worker:
   - `transcode_one(jpg, replace=True)` del módulo compartido.
   - Errores se loggean a `/var/log/anpr/jxl-watcher.log`.

**`docker-compose.yml` (agregar al existente):**

```yaml
anpr-jxl-watcher:
  build:
    context: .
    dockerfile: anpr_jxl_watcher.Dockerfile
  network_mode: host
  env_file: .env
  volumes:
    - ./app/anpr_images:/app/anpr_images
  depends_on:
    - mariadb
  restart: always
  healthcheck:
    test: ["CMD", "test", "-f", "/tmp/jxl-watcher-alive"]
    interval: 30s
    timeout: 5s
    retries: 3
```

El watcher escribe `mtime` a `/tmp/jxl-watcher-alive` cada 10 s en su main
loop; healthcheck falla si está stale.

### D. CLI batch (ya construido en Fase 1)

`scripts/transcode_jxl.py` — sin cambios. Usar para:

- Sweep histórico (one-shot, foreground en screen/tmux).
- Re-procesar archivos huérfanos que el watcher no procesó (en caso de
  incidente).

## Fases de entrega

### Fase 1 — Alivio urgente de disco

**Objetivo:** liberar ~20 GB del corpus existente lo antes posible.

**Tareas:**
1. Implementar el fallback JXL→djxl→JPG en `serve_image()`.
2. (Si aplica) Replicar en cualquier otro endpoint que sirva imágenes por
   nombre.
3. Build + redeploy de los containers afectados.
4. Verificación manual obligatoria:
   - Crear un `.jxl` de prueba a partir de un `.jpg` existente.
   - Eliminar el `.jpg`.
   - Recargar el dashboard, verificar que la imagen se sigue mostrando.
   - Solo si pasa, continuar al sweep.
5. Sweep histórico con `scripts/transcode_jxl.py --replace --age-min-hours 24
   --throttle-ms 100 -v` en foreground (screen/tmux). Tiempo estimado: ~6.7 h.
6. Reportar ahorro real (`du -sh /app/anpr_images` antes/después).

**Criterio de éxito:** disco usado baja en ~20 GB; cero errores 404 en logs del
backend; 100/100 reconstrucciones bit-exact reportadas por el script.

### Fase 2 — Compresión automática de ingesta nueva

**Objetivo:** reducir el crecimiento futuro de 745 MB/día a ~620 MB/día sin
intervención manual.

**Tareas:**
1. Migration: crear tabla `app_settings`. Idempotente.
2. Refactor: extraer lógica de transcode del CLI a `app/jxl_transcode.py`.
3. Endpoints `GET/PUT /api/admin/settings` en db-manager + mirror en anpr-web.
4. UI: tab "Configuración" en admin con toggle.
5. i18n: strings `admin.settings.*` en ambos JSON, paridad verificada.
6. Servicio watcher: `anpr_jxl_watcher.py`, Dockerfile, entry en compose.
7. Tests manuales: toggle off → verify no se procesa; toggle on → drop manual
   de un .jpg → verify se convierte en segundos.

**Criterio de éxito:** con toggle activo y un día típico de ingesta, el
crecimiento medido baja ~17%; latencia del dashboard sin degradación notable.

## Fuera de scope (v1)

- **Cache LRU en backend.** Documentado como recomendación futura. Trigger:
  más viewers concurrentes o quejas de lentitud. Implementación estimada: ~30
  líneas con `cachetools.LRUCache(maxsize=200)`, costo ~100 MB RAM.
- Tiered storage a Backblaze B2 (planeado por separado en ROADMAP).
- Reactivar `.jxl` → `.jpg` masivo (rollback existe vía `djxl`, no se
  automatiza).
- Compresión condicional por cámara / por horario / por placa.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| `--replace` corre antes de que el backend soporte JXL | Paso 4 de Fase 1 (verificación manual) es bloqueante. No se ejecuta el sweep sin pasar esa prueba. |
| Bit-exact fail intermitente en alguna imagen | Script y watcher dejan el `.jpg` intacto si `verify_fail`; reportan el archivo para investigación posterior. |
| Watcher pierde eventos durante restart | Sweep inicial al startup busca `.jpg` sin pareja `.jxl` y los encola. |
| Toggle no se respeta (bug) | UI re-lee y muestra estado actual tras cada PUT. Se valida en testing manual de Fase 2. |
| `djxl` spawn overhead alto bajo carga | Cache LRU está fuera de scope pero documentado. Si se activa la queja, ~30 líneas + 100 MB RAM lo resuelven. |
| Servicio watcher crash silencioso | Healthcheck con archivo touch + `restart: always`. |
| Pico de CPU durante sweep histórico | `--throttle-ms 100` agrega ~10 % de overhead pero baja contención de I/O. Se corre fuera de horario pico. |
| Doble-procesamiento si el listener escribe el .jpg en chunks | Watcher escucha `IN_CLOSE_WRITE` (fired solo al close); no se gatilla en escritura parcial. |

## Testing strategy

**Componente A:**
- Unit test: mockear filesystem, validar que `.jpg` ausente + `.jxl` presente
  devuelve bytes JPG válidos con `Content-Type: image/jpeg`.
- E2E: subir un `.jxl` real al volumen, cargar el dashboard, verificar render
  visual.

**Sweep histórico (CLI Fase 1):**
- Dry-run completo (sin escribir) sobre producción primero.
- `--limit 100` real, validar 100/100 verified.
- Solo entonces full sweep.

**Componente B:**
- Unit: PUT con valor inválido → 400. PUT como non-admin → 403. PUT correcto
  → 200, GET refleja cambio.

**Componente C:**
- Manual: tocar un `.jpg` al volumen monitoreado, verificar que aparece
  `.jxl` en pocos segundos.
- Toggle off → tocar otro `.jpg`, verificar que no se procesa.
- Restart del watcher con varios `.jpg` huérfanos → verificar sweep inicial.

**Regresión:**
- Cargar dashboard con N eventos mezclados `.jpg`/`.jxl`, comparar tiempo de
  carga con baseline sin JXL.

## Rollback

**Fase 1:**
- Backend: revertir cambios en `serve_image()`. Si ya hay `.jxl` en disco sin
  `.jpg`, reconstruir manualmente con `for f in *.jxl; do djxl "$f"
  "${f%.jxl}.jpg"; done`. Bit-exact.
- Sweep no es "deshacer" — los `.jxl` son reconstruibles a JPG byte-a-byte.

**Fase 2:**
- Toggle off en UI → watcher entra en modo idle (sigue corriendo, no procesa).
- `docker-compose stop anpr-jxl-watcher` → para el servicio completo.
- `DROP TABLE app_settings` solo si se decide remover el feature de toggles.
