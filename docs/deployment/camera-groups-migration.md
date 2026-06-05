# Runbook de migración: Grupos de cámaras y control de acceso

**Aplica al sistema en producción**: `/root/anpr-camera-dev`
**Fecha del runbook**: 2026-05-17
**Spec asociado**: [`docs/superpowers/specs/2026-05-17-camera-groups-and-access-design.md`](../superpowers/specs/2026-05-17-camera-groups-and-access-design.md)

---

## Propósito

Este documento guía paso a paso la migración del sistema ANPR en producción al nuevo modelo de grupos de cámaras + control de acceso por grupo. Cubre el caso donde la DB ya tiene usuarios viewers funcionando que necesitan ser reasignados al nuevo modelo.

Para una **instalación nueva** (fresh deploy en un servidor limpio), no aplica este runbook. En ese caso, basta con desplegar el código nuevo, ejecutar `./setup.sh start`, crear el admin inicial, y a partir de ahí crear grupos y viewers desde la UI a medida que se necesiten.

---

## Cuándo aplicar

Aplicar este runbook cuando se cumplan ambas condiciones:

1. El servidor ya tiene viewers (`role='viewer'`) creados en la tabla `user`.
2. Se va a desplegar la versión del código que incluye la feature de grupos.

---

## Pre-flight checklist

Antes de empezar:

- [ ] Tener acceso SSH como root (o sudo) al servidor.
- [ ] Confirmar que estoy en `/root/anpr-camera-dev`.
- [ ] Tener listo el mapeo conceptual de viewers a sus cámaras esperadas (qué viewer debe ver qué cámaras). Si no lo tengo claro, anotarlo antes de empezar — durante la ventana de migración los viewers están temporalmente sin acceso, no es momento de andar averiguando esto.
- [ ] Elegir una ventana de tiempo de bajo uso (madrugada, fin de semana). La ventana de impacto es típicamente 10-15 min, pero conviene tener margen.
- [ ] Tener una sesión incógnita lista para validar con credenciales de un viewer.

---

## Paso 1 — Respaldos pre-deploy (obligatorio)

Sin saltarse este paso. Si algo sale mal sin respaldo, no hay rollback.

```bash
TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/root/anpr-camera-dev/backups
ROOT_PASS=$(sudo grep "^MYSQL_ROOT_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)

# 1.1: Dump completo de la DB (esquema + datos, incluyendo users, eventos, cameras)
sudo docker exec anpr-mariadb mysqldump \
  -u root -p"$ROOT_PASS" \
  --single-transaction --routines --triggers --events \
  --default-character-set=utf8mb4 \
  anpr_events 2>/dev/null | sudo tee "$BACKUP_DIR/anpr_events_pre_groups_${TS}.sql" > /dev/null
sudo gzip "$BACKUP_DIR/anpr_events_pre_groups_${TS}.sql"

# 1.2: Tar con los archivos de código que la nueva versión va a modificar
sudo tar czf "$BACKUP_DIR/code_pre_groups_${TS}.tar.gz" -C /root/anpr-camera-dev \
  app/anpr_db_manager.py \
  app/anpr_web.py \
  app/models.py \
  app/templates/admin.html \
  app/templates/index.html

# 1.3: Verificar respaldos
sudo ls -lah "$BACKUP_DIR/anpr_events_pre_groups_${TS}.sql.gz" \
              "$BACKUP_DIR/code_pre_groups_${TS}.tar.gz"
echo "TS=$TS"
```

**Validación**:
- Ambos archivos deben tener tamaño > 0.
- El dump comprimido típicamente pesa 5-10 MB. Si pesa < 1 MB, algo falló — revisar.
- El tar de código típicamente pesa 25-40 KB.

**Anota el valor de `TS`** — lo vas a necesitar si hay que hacer rollback. Apúntalo en un lugar seguro.

---

## Paso 2 — Deploy del código

(Asumiendo que el código nuevo está mergeado a `master` y pulled localmente.)

```bash
cd /root/anpr-camera-dev

# Rebuild de las imágenes (los archivos .py están baked, restart no es suficiente)
sudo docker-compose build anpr-db-manager anpr-web

# Recreate de los contenedores con la nueva imagen
sudo docker-compose up -d anpr-db-manager anpr-web

# Esperar a que el db-manager arranque y ejecute initialize_database()
sleep 20

# Verificar health
curl -s -o /dev/null -w "anpr-db-manager: HTTP %{http_code}\n" http://localhost:5001/health
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Ambos deben responder `HTTP 200`. Si alguno falla, ir a la sección de **Rollback** abajo.

**Validar que las nuevas tablas se crearon**:

```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SHOW TABLES LIKE '%group%';" 2>&1 | grep -v "Using a password"
```

Esperado: aparecen `camera_groups`, `camera_group_members`, `user_camera_groups`. Si faltan, ir a Rollback Nivel 2.

**En este punto los 4 viewers ya están temporalmente sin acceso** (su `allowed_camera_ids` regresa lista vacía). Continuar de inmediato con el Paso 3.

---

## Paso 3 — Crear grupos en la UI

Entrar al panel admin: `https://<tu-dominio>/admin` (login como `admin` o `Gabriel`).

Click en el nuevo tab **"Grupos"**.

Para cada cliente/sitio:

1. Click **"+ Crear grupo"**.
2. Nombre: descriptivo (ej. `Residencial Cinco Ventanas`, `Casa Jorge Carrillo`). Si te equivocas, se puede renombrar después.
3. Descripción: opcional.
4. Marcar las cámaras que pertenecen a este grupo.
5. Click **"Guardar"**.

Repetir hasta tener todos los grupos definidos.

**Validación rápida**: cada cámara debería estar en al menos un grupo (no es obligatorio, pero si una cámara queda sin grupos, ningún viewer la va a ver, solo admins).

---

## Paso 4 — Asignar grupos a viewers existentes

Ir al tab **"Users"**.

Para cada viewer (los 4 actuales: visorjorgecarrillo, visorgilbertvalverde, visorjhonnelizondo, visorbobschlesinger):

1. Click ✏️ junto al viewer.
2. En el modal, sección **"Grupos asignados"**, marcar el(los) grupo(s) que le corresponden según el mapeo conceptual que anotaste en el pre-flight.
3. Click **"Guardar"**.

⚠️ El warning visual aparece si dejas 0 grupos marcados. Eso no es bloqueante — si lo dejas así, el viewer ve nada (intencional). Pero verifica que realmente quieras eso.

---

## Paso 5 — Validación end-to-end

En una pestaña **incógnita** (para no interferir con tu sesión admin):

1. Login como uno de los viewers (cualquiera de los 4).
2. Confirmar que el dashboard carga.
3. Confirmar que el dropdown de filtro de cámaras muestra **solo** las cámaras que asignaste a sus grupos.
4. Confirmar que la tabla de eventos solo muestra eventos de esas cámaras.
5. (Opcional pero recomendado) Repetir con un segundo viewer distinto.

**Validación adicional vía DB**:

```bash
DB_PASS=$(sudo grep "^MYSQL_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
# Cada viewer debería tener al menos un grupo asignado
sudo docker exec anpr-mariadb mysql -u anpr_user -p"$DB_PASS" anpr_events -e \
  "SELECT u.username, COUNT(ucg.group_id) AS group_count
   FROM user u LEFT JOIN user_camera_groups ucg ON ucg.user_id = u.id
   WHERE u.role = 'viewer'
   GROUP BY u.id, u.username
   ORDER BY u.username;" 2>&1 | grep -v "Using a password"
```

Esperado: cada viewer con `group_count >= 1`. Si alguno aparece con 0, ese viewer aún está sin acceso — completar paso 4 para él.

---

## Paso 6 — Cierre

- [ ] Comunicar a los viewers afectados que el sistema vuelve a estar disponible y que ahora solo verán las cámaras que les corresponden (si es relevante avisar).
- [ ] Anotar en `docs/field-investigations.md` o donde corresponda cualquier issue post-migración.
- [ ] Conservar los respaldos del Paso 1 al menos 30 días. Apuntar el TS por si hay que hacer rollback retroactivo.

Migración completa.

---

## Rollback

Tres niveles según la severidad del problema. Empezar siempre por el menos invasivo (Nivel 1) y subir solo si es necesario.

Reemplazar `<TS>` en los comandos con el timestamp que anotaste en el Paso 1.

### Nivel 1 — Restaurar solo el código

Aplicar cuando:
- La UI nueva está rota.
- Algún endpoint nuevo crashea.
- Los viewers no pueden acceder pero la DB está sana.

```bash
TS=<TS>  # reemplaza con tu timestamp del Paso 1

# Restaurar archivos de código desde el tar de respaldo
sudo mkdir -p /tmp/restore
sudo tar xzf /root/anpr-camera-dev/backups/code_pre_groups_${TS}.tar.gz -C /tmp/restore/
sudo cp -r /tmp/restore/app/* /root/anpr-camera-dev/app/

# Rebuild de contenedores (no basta restart — archivos baked en image)
cd /root/anpr-camera-dev
sudo docker-compose build anpr-db-manager anpr-web
sudo docker-compose up -d anpr-db-manager anpr-web

# Verificar health
sleep 20
curl -s -o /dev/null -w "anpr-db-manager: HTTP %{http_code}\n" http://localhost:5001/health
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

**Efecto**: el código vuelve a la versión previa. Las 3 tablas nuevas siguen en la DB pero ningún código las consulta — no causan problema. Los viewers vuelven a ver todas las cámaras (comportamiento previo). Las tablas se pueden borrar después con calma si se quiere.

### Nivel 2 — Restaurar código + borrar tablas nuevas

Aplicar cuando:
- El esquema quedó en estado inconsistente (ej. un constraint falló a mitad).
- Querés dejar la DB tal cual estaba antes del deploy.

```bash
TS=<TS>  # reemplaza con tu timestamp del Paso 1

# 1) Ejecutar Nivel 1 primero (restaurar código)
# (ver arriba)

# 2) Borrar las 3 tablas nuevas
ROOT_PASS=$(sudo grep "^MYSQL_ROOT_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo docker exec anpr-mariadb mysql -u root -p"$ROOT_PASS" anpr_events -e \
  "SET FOREIGN_KEY_CHECKS=0;
   DROP TABLE IF EXISTS user_camera_groups;
   DROP TABLE IF EXISTS camera_group_members;
   DROP TABLE IF EXISTS camera_groups;
   SET FOREIGN_KEY_CHECKS=1;"

# 3) Verificar
sudo docker exec anpr-mariadb mysql -u root -p"$ROOT_PASS" anpr_events -e \
  "SHOW TABLES LIKE '%group%';"
```

Esperado: la query final no devuelve filas. Si quedó alguna tabla, repetir el DROP.

**Efecto**: la DB queda exactamente como antes del deploy. Datos de eventos, users, cameras intactos.

### Nivel 3 — Restore completo desde dump

Aplicar cuando:
- Hubo corrupción de datos.
- Niveles 1 y 2 no resolvieron el problema.
- Solo si todo lo demás falló — esta es la opción nuclear.

```bash
TS=<TS>  # reemplaza con tu timestamp del Paso 1

# 1) Ejecutar Nivel 1 (restaurar código)
# (ver arriba)

# 2) Restaurar el dump completo de la DB
ROOT_PASS=$(sudo grep "^MYSQL_ROOT_PASSWORD=" /root/anpr-camera-dev/.env | cut -d= -f2)
sudo zcat /root/anpr-camera-dev/backups/anpr_events_pre_groups_${TS}.sql.gz | \
  sudo docker exec -i anpr-mariadb mysql -u root -p"$ROOT_PASS" anpr_events

# 3) Rebuild + restart de containers (Nivel 1 ya lo hizo, repetir por seguridad)
cd /root/anpr-camera-dev
sudo docker-compose restart anpr-db-manager anpr-web

# 4) Verificar
sleep 20
curl -s -o /dev/null -w "anpr-db-manager: HTTP %{http_code}\n" http://localhost:5001/health
```

**Efecto**: el sistema regresa al estado exacto del Paso 1 (pre-deploy). Se pierden los eventos que entraron durante la ventana del deploy y migración (típicamente pocos, dependiendo de cuánto duró).

---

## Limpieza de respaldos

Cumplido el periodo de retención (30 días por defecto), los respaldos se pueden:

- Borrar localmente: `sudo rm /root/anpr-camera-dev/backups/{anpr_events,code}_pre_groups_<TS>.*`
- O archivar a Backblaze cuando esté implementada Phase 6 del ROADMAP.

No borrar antes de los 30 días salvo problema de espacio en disco crítico.
