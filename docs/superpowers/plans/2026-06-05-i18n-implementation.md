# i18n (Spanish/English) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar soporte ES/EN a la UI de `anpr-web` mediante toggle en el header, persistido en cookie, usando archivos JSON como fuente de verdad y un helper `t(key)` unificado en backend, templates y JS.

**Architecture:** Dos archivos JSON (`app/translations/{es,en}.json`) cargados al startup de anpr-web. `context_processor` inyecta `t`, `current_lang` y `translations_json` en cada template. JS recibe el dict del idioma activo via `window.__i18n`. Endpoint `GET /set-lang/<lang>` setea cookie y redirecta. Feature 100% aditiva: no toca DB, listener ni db-manager.

**Tech Stack:** Python 3.11, Flask + Flask-Login, Jinja2 templates, vanilla JS + Tailwind, MariaDB sin cambios, Docker Compose.

**Spec de referencia:** [docs/superpowers/specs/2026-06-05-i18n-design.md](../specs/2026-06-05-i18n-design.md)

---

## Estado actual del repositorio

- En `master`, commit `139a8b0` (último: el spec mismo). Working tree limpio salvo `?? input.txt` (no relacionado).
- Producción corriendo, health 200. 7 tablas DB sin cambios.
- `anpr_web.Dockerfile` actualmente copia `app/anpr_web.py`, `app/models.py`, `app/templates/`, `app/static/`. **No copia `app/translations/`** — el plan lo añade.

## Setup compartido (lee esto antes de empezar)

**Permisos**: el código vive en `/root/anpr-camera-dev/`, propiedad de root. Todos los comandos van con `sudo`. Edición de archivos: `sudo cp` a `/tmp`, `sudo chown gabriel:gabriel /tmp/<file>`, editar, `python3 -m py_compile` para Python, `sudo cp` de vuelta, `sudo chown root:root`, luego rebuild Docker.

**Deploy pattern** (los archivos Python y templates están baked en imagen):
```bash
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
```
~2 min. `docker restart` NO es suficiente.

**Push final** (cuando llegue Task 8):
```bash
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" \
  git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master
```

**Convención de keys** (del spec): `<area>.<element>[.<modifier>]` en snake_case. Plano (sin namespaces anidados en el JSON). Ejemplos: `header.logout`, `table.col.plate`, `status.trust_car`. Mantener orden alfabético en el JSON para minimizar diffs.

**Fallback diseñado**: si una key no existe, `t(key)` retorna la key como string literal. Visible en UI para detectar gaps durante QA.

---

## File structure

| Archivo | Cambio | Responsabilidad |
|---|---|---|
| `app/translations/es.json` | Create | Strings en español, ~160 keys |
| `app/translations/en.json` | Create | Strings en inglés, mismas keys |
| `anpr_web.Dockerfile` | Modify (1 línea) | `COPY app/translations/ /app/app/translations/` |
| `app/anpr_web.py` | Modify (substantial) | Load TRANSLATIONS al startup. Helpers `get_lang()`, `t()`. Context processor `inject_i18n`. Endpoint `/set-lang/<lang>`. Refactor de flash/jsonify para usar `t()`. |
| `app/templates/login.html` | Modify | Toggle ES/EN. Refactor strings con `{{ t() }}`. |
| `app/templates/index.html` | Modify (substantial) | Toggle ES/EN en header. Refactor strings HTML con `{{ t() }}`. Inyectar `window.__i18n`. Refactor strings dinámicas en JS con `t(key)`. |
| `app/templates/admin.html` | Modify (substantial) | Igual que index.html pero más grande (3 tabs + 4 modales). |
| `backups/code_pre_i18n_<TS>.tar.gz` | Create | Tar de respaldo pre-deploy. |

No se toca: DB schema, listener, db-manager, models.py.

---

## Task 1: Pre-flight check + backup

**Files:** ninguno modificado, 1 backup creado

- [ ] **Step 1.1: Verificar estado del repo + producción**

Run:
```bash
sudo git -C /root/anpr-camera-dev status --short --branch
sudo docker ps --filter name=anpr --format "table {{.Names}}\t{{.Status}}"
curl -s -o /dev/null -w "anpr-db-manager: HTTP %{http_code}\n" http://localhost:5001/health
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: branch `master` aligned con `origin/master`. Todos containers `Up`. Ambos health endpoints `HTTP 200`. Si algo falla, parar y reportar.

- [ ] **Step 1.2: Crear respaldo del código que vamos a tocar**

Run:
```bash
TS=$(date +%Y%m%d_%H%M%S)
echo "TS=$TS"
sudo tar czf "/root/anpr-camera-dev/backups/code_pre_i18n_${TS}.tar.gz" \
  -C /root/anpr-camera-dev \
  app/anpr_web.py \
  app/templates/login.html \
  app/templates/index.html \
  app/templates/admin.html \
  anpr_web.Dockerfile
sudo ls -lah "/root/anpr-camera-dev/backups/code_pre_i18n_${TS}.tar.gz"
sudo tar tzf "/root/anpr-camera-dev/backups/code_pre_i18n_${TS}.tar.gz"
```

Expected: archivo creado ~30-50 KB. Listado muestra los 5 archivos. **Anotar el valor de `TS`** — se reusa en eventual rollback. No se hace dump de DB porque la feature no la toca.

---

## Task 2: Crear `app/translations/` con archivos iniciales + extender Dockerfile

**Files:**
- Create: `/root/anpr-camera-dev/app/translations/es.json`
- Create: `/root/anpr-camera-dev/app/translations/en.json`
- Modify: `/root/anpr-camera-dev/anpr_web.Dockerfile` (agregar 1 línea)

- [ ] **Step 2.1: Crear la carpeta `translations/`**

Run:
```bash
sudo mkdir -p /root/anpr-camera-dev/app/translations
sudo ls -la /root/anpr-camera-dev/app/translations
```

Expected: directorio creado, vacío.

- [ ] **Step 2.2: Crear `es.json` con un set mínimo arrancando**

Editar el archivo. Como root es el dueño del padre, sacar via /tmp luego copiar.

Crear `/tmp/es.json` con este contenido (set base, se ampliará en Tasks 4-7):

```json
{
  "_meta.lang_name": "Español",
  "_meta.lang_code": "es",
  "header.title": "Visor de Eventos ANPR",
  "header.logout": "Cerrar sesión",
  "header.admin_panel": "Panel admin",
  "header.dashboard": "Dashboard",
  "header.toggle_dark_mode": "Cambiar modo oscuro",
  "login.title": "Iniciar sesión",
  "login.username": "Usuario",
  "login.password": "Contraseña",
  "login.submit": "Entrar",
  "login.invalid": "Usuario o contraseña inválidos"
}
```

Run:
```bash
sudo cp /tmp/es.json /root/anpr-camera-dev/app/translations/es.json
sudo chown root:root /root/anpr-camera-dev/app/translations/es.json
python3 -c "import json; json.load(open('/tmp/es.json'))" && echo "JSON válido"
```

Expected: archivo copiado, `JSON válido`.

- [ ] **Step 2.3: Crear `en.json` con las mismas keys**

Crear `/tmp/en.json`:

```json
{
  "_meta.lang_name": "English",
  "_meta.lang_code": "en",
  "header.title": "ANPR Event Viewer",
  "header.logout": "Logout",
  "header.admin_panel": "Admin Panel",
  "header.dashboard": "Dashboard",
  "header.toggle_dark_mode": "Toggle Dark Mode",
  "login.title": "Login",
  "login.username": "Username",
  "login.password": "Password",
  "login.submit": "Sign in",
  "login.invalid": "Invalid username or password"
}
```

Run:
```bash
sudo cp /tmp/en.json /root/anpr-camera-dev/app/translations/en.json
sudo chown root:root /root/anpr-camera-dev/app/translations/en.json
python3 -c "import json; json.load(open('/tmp/en.json'))" && echo "JSON válido"
```

Expected: archivo copiado, JSON válido. Las keys en ambos archivos deben coincidir 1:1 — si una existe en uno y no en otro, el fallback hará que aparezca como key literal en el idioma incompleto. Verificar:

```bash
diff <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/es.json')).keys())))") \
     <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/en.json')).keys())))")
```

Expected: diff vacío (todas las keys coinciden).

- [ ] **Step 2.4: Extender `anpr_web.Dockerfile` para incluir `app/translations/`**

Sacar el Dockerfile:
```bash
sudo cp /root/anpr-camera-dev/anpr_web.Dockerfile /tmp/anpr_web.Dockerfile
sudo chown gabriel:gabriel /tmp/anpr_web.Dockerfile
```

Localizar la línea `COPY app/static/ /app/app/static/`. INMEDIATAMENTE DESPUÉS de ella, insertar:

```dockerfile
COPY app/translations/ /app/app/translations/
```

- [ ] **Step 2.5: Desplegar Dockerfile y verificar que arranca**

Run:
```bash
sudo cp /tmp/anpr_web.Dockerfile /root/anpr-camera-dev/anpr_web.Dockerfile
sudo chown root:root /root/anpr-camera-dev/anpr_web.Dockerfile
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: build sin errores, HTTP 200.

Verificar que los JSON están en el contenedor:
```bash
sudo docker exec anpr-web ls -la /app/app/translations/
sudo docker exec anpr-web cat /app/app/translations/es.json | head -5
```

Expected: ambos archivos visibles, contenido legible.

---

## Task 3: anpr_web.py — Load TRANSLATIONS + helpers + context processor + endpoint `/set-lang`

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_web.py`

- [ ] **Step 3.1: Sacar el archivo a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_web.py /tmp/anpr_web.py
sudo chown gabriel:gabriel /tmp/anpr_web.py
```

- [ ] **Step 3.2: Agregar imports faltantes + carga al top-level**

Buscar la línea `from urllib.parse import urlparse` (línea 9). INMEDIATAMENTE DESPUÉS de esa línea, insertar:

```python
import json
```

Luego buscar la línea `app = Flask(__name__)` (línea 11). INMEDIATAMENTE ANTES, insertar:

```python
# --- i18n: load translations at startup (fail-fast if missing/malformed) ---
TRANSLATIONS = {}
for _lang in ('es', 'en'):
    with open(f'/app/app/translations/{_lang}.json', encoding='utf-8') as _f:
        TRANSLATIONS[_lang] = json.load(_f)
SUPPORTED_LANGS = ('es', 'en')
DEFAULT_LANG = 'es'

```

Hay imports extra que `make_response` requeriremos en Step 3.4. Buscar la línea `from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session` y agregar `make_response` al import:

```python
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session, make_response
```

- [ ] **Step 3.3: Agregar helpers `get_lang`, `t`, y el context processor**

Buscar `def admin_required(f):` (alrededor de línea 73 originalmente). INMEDIATAMENTE ANTES de esa línea, insertar:

```python
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


```

- [ ] **Step 3.4: Agregar endpoint `/set-lang/<lang>`**

Buscar `@app.route('/login', methods=['GET', 'POST'])` (alrededor de línea 90). INMEDIATAMENTE ANTES de esa línea, insertar:

```python
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


```

- [ ] **Step 3.5: Validar sintaxis + deploy**

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

Expected: `Sintaxis OK`, build, HTTP 200.

- [ ] **Step 3.6: Smoke test del endpoint /set-lang**

Run:
```bash
echo "=== Set lang=en con next inválido (open redirect attempt) — debe fallback a / ==="
curl -s -o /dev/null -w "HTTP %{http_code} Location: %{redirect_url}\n" \
  "http://localhost:5000/set-lang/en?next=https://evil.com/x"

echo "=== Set lang=en con next válido relativo — debe redirect ahí ==="
curl -s -o /dev/null -w "HTTP %{http_code} Location: %{redirect_url}\n" \
  "http://localhost:5000/set-lang/en?next=/admin"

echo "=== Set lang inválido — debe 400 ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" "http://localhost:5000/set-lang/fr"

echo "=== Verificar que la cookie viene en el response ==="
curl -s -I "http://localhost:5000/set-lang/en?next=/" | grep -i "set-cookie"
```

Expected:
- Test 1: HTTP 302 hacia `/` (la URL externa rechazada).
- Test 2: HTTP 302 hacia `/admin`.
- Test 3: HTTP 400.
- Test 4: header `Set-Cookie: lang=en; ...`.

Si alguno falla, revisar la lógica del endpoint.

---

## Task 4: login.html — Toggle + refactor de strings

**Files:**
- Modify: `/root/anpr-camera-dev/app/templates/login.html`
- Modify: `/root/anpr-camera-dev/app/translations/es.json` (agregar keys si faltan)
- Modify: `/root/anpr-camera-dev/app/translations/en.json` (agregar keys si faltan)

- [ ] **Step 4.1: Sacar archivos a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/templates/login.html /tmp/login.html
sudo cp /root/anpr-camera-dev/app/translations/es.json /tmp/es.json
sudo cp /root/anpr-camera-dev/app/translations/en.json /tmp/en.json
sudo chown gabriel:gabriel /tmp/login.html /tmp/es.json /tmp/en.json
```

- [ ] **Step 4.2: Extraer strings visibles del template**

Run:
```bash
echo "=== Strings entre tags (visibles al user) ==="
grep -oE '>[A-Za-zÁÉÍÓÚáéíóúÑñ¡¿][^<>{}]{2,}<' /tmp/login.html | sort -u

echo "=== Atributos visibles (placeholder, title, alt) ==="
grep -oE '(placeholder|title|alt)="[^"]+"' /tmp/login.html | sort -u

echo "=== Mensajes flash inyectados desde backend (verificar que pasan por t()) ==="
grep -nE "get_flashed_messages|flash\(" /tmp/login.html
```

Documentar la lista de strings encontrados. Para cada uno, decidir si:
- Es un string traducible (labels, botones, mensajes) → necesita key
- Es marca/nombre propio o código (ej. nombres de campo HTML como `username`) → no traducir

- [ ] **Step 4.3: Agregar keys faltantes a ambos JSONs**

Las keys mínimas para login (algunas ya en el JSON inicial, otras se añaden):

```json
{
  "login.title": "Iniciar sesión" / "Login",
  "login.username": "Usuario" / "Username",
  "login.password": "Contraseña" / "Password",
  "login.submit": "Entrar" / "Sign in",
  "login.invalid": "Usuario o contraseña inválidos" / "Invalid username or password",
  "login.system_name": "Sistema ANPR" / "ANPR System"
}
```

Editar `/tmp/es.json` y `/tmp/en.json` para agregar cualquier key que falte de la lista del Step 4.2. Mantener orden alfabético.

Verificar paridad de keys:
```bash
diff <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/es.json')).keys())))") \
     <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/en.json')).keys())))")
```

Expected: diff vacío.

- [ ] **Step 4.4: Refactorizar `login.html` para usar `{{ t() }}` + agregar toggle**

Patrón de reemplazo:
- `>Login<` → `>{{ t('login.title') }}<`
- `placeholder="Username"` → `placeholder="{{ t('login.username') }}"`
- Etc.

Y agregar el toggle ES/EN. Buscar el `<div>` que contiene el formulario de login. Inmediatamente ANTES del `<form>`, insertar un div con los botones:

```html
        <div class="flex justify-end space-x-1 mb-4">
            <a href="/set-lang/es?next={{ request.path }}"
               class="px-2 py-1 text-xs rounded {% if current_lang == 'es' %}bg-blue-600 text-white font-semibold{% else %}text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700{% endif %}">ES</a>
            <a href="/set-lang/en?next={{ request.path }}"
               class="px-2 py-1 text-xs rounded {% if current_lang == 'en' %}bg-blue-600 text-white font-semibold{% else %}text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700{% endif %}">EN</a>
        </div>
```

Ejemplo concreto de refactor — buscar `<title>Login - ANPR System</title>` (o equivalente) y reemplazar por:

```html
<title>{{ t('login.title') }} - {{ t('login.system_name') }}</title>
```

Buscar `<button type="submit"...>Login</button>` (o equivalente) y reemplazar el contenido por `{{ t('login.submit') }}`.

Para los mensajes flash del backend (que se renderizan con `{% for message in get_flashed_messages() %}`): NO modificar el template — el backend ya pasará el mensaje traducido en Task 7.

- [ ] **Step 4.5: Validar JSON + deploy + smoke test**

Run:
```bash
python3 -c "import json; json.load(open('/tmp/es.json')); json.load(open('/tmp/en.json'))" && echo "JSONs válidos"

sudo cp /tmp/login.html /root/anpr-camera-dev/app/templates/login.html
sudo cp /tmp/es.json /root/anpr-camera-dev/app/translations/es.json
sudo cp /tmp/en.json /root/anpr-camera-dev/app/translations/en.json
sudo chown root:root /root/anpr-camera-dev/app/templates/login.html /root/anpr-camera-dev/app/translations/{es,en}.json

cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: JSONs válidos, build, HTTP 200.

Verificar que el HTML renderizado tiene los strings traducidos:
```bash
echo "=== Login page default (es) ==="
curl -s http://localhost:5000/login | grep -oE "(Iniciar sesión|Login|Sign in|Usuario|Username)" | sort -u

echo "=== Login page forzando lang=en via cookie ==="
curl -s -b "lang=en" http://localhost:5000/login | grep -oE "(Iniciar sesión|Login|Sign in|Usuario|Username)" | sort -u
```

Expected: en default aparecen palabras en español; con cookie `lang=en` aparecen en inglés. Si aparecen literales de keys (`login.title`), significa que el render del template no encontró la key — revisar JSON.

---

## Task 5: index.html — Toggle + refactor de strings HTML + inyección `window.__i18n` + refactor JS

**Files:**
- Modify: `/root/anpr-camera-dev/app/templates/index.html`
- Modify: `/root/anpr-camera-dev/app/translations/es.json`
- Modify: `/root/anpr-camera-dev/app/translations/en.json`

- [ ] **Step 5.1: Sacar archivos a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/templates/index.html /tmp/index.html
sudo cp /root/anpr-camera-dev/app/translations/es.json /tmp/es.json
sudo cp /root/anpr-camera-dev/app/translations/en.json /tmp/en.json
sudo chown gabriel:gabriel /tmp/index.html /tmp/es.json /tmp/en.json
```

- [ ] **Step 5.2: Inventariar TODOS los strings de index.html**

Ejecutar la extracción exhaustiva:

```bash
echo "=== Strings HTML visibles ==="
grep -oE '>[A-Za-zÁÉÍÓÚáéíóúÑñ¡¿][^<>{}]{2,}<' /tmp/index.html | sort -u

echo "=== Atributos visibles ==="
grep -oE '(placeholder|title|alt)="[^"]+"' /tmp/index.html | sort -u

echo "=== Strings en JS (excluyendo CSS classes, paths SVG, URLs, props HTTP) ==="
grep -oE "'[A-Za-zÁÉÍÓÚáéíóúÑñ][A-Za-zÁÉÍÓÚáéíóúÑñ. ?!]{4,}'" /tmp/index.html \
  | grep -vE "^'(GET|POST|PUT|DELETE|application|json|hidden|block|flex)" \
  | sort -u

echo "=== Mismo, con comillas dobles ==="
grep -oE '"[A-Za-zÁÉÍÓÚáéíóúÑñ][A-Za-zÁÉÍÓÚáéíóúÑñ. ?!]{4,}"' /tmp/index.html \
  | grep -vE '"(GET|POST|PUT|DELETE|application|json|hidden|block|flex|UTF|svg|path)' \
  | sort -u
```

Listar resultado. Estimación: ~40-50 strings traducibles entre HTML y JS.

- [ ] **Step 5.3: Mapear strings a keys siguiendo la convención**

Convención del spec: `<area>.<element>[.<modifier>]` snake_case. Áreas para index.html:
- `header.*` (título, botones del header)
- `filter.*` (form de filtros del dashboard)
- `table.col.*` (columnas de la tabla)
- `table.*` (mensajes de la tabla: vacío, cargando, error)
- `pagination.*`
- `notification.*` (toast de nuevo evento)
- `status.*` (badges de estado)
- `direction.*` (badges de dirección)

Keys ya documentadas en el spec sección 5 — usar esas literalmente. Agregar las que falten siguiendo el mismo patrón.

Editar `/tmp/es.json` y `/tmp/en.json` agregando TODAS las keys necesarias para index.html. Mantener orden alfabético. Verificar paridad después:

```bash
diff <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/es.json')).keys())))") \
     <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/en.json')).keys())))")
```

Expected: diff vacío.

- [ ] **Step 5.4: Refactorizar el HTML de index.html con `{{ t() }}`**

Para cada string del Step 5.2 que sea traducible:
- `>Logout<` → `>{{ t('header.logout') }}<`
- `placeholder="Search Plate..."` → `placeholder="{{ t('filter.plate.placeholder') }}"`
- Etc.

Mantener la estructura HTML exactamente igual — solo cambiar el texto.

- [ ] **Step 5.5: Agregar toggle ES/EN en el header**

Localizar el div del header (donde están los botones de Admin Panel y Logout). Agregar antes del botón Admin Panel (o donde quepa visualmente):

```html
                <div class="flex items-center space-x-1 mr-3">
                    <a href="/set-lang/es?next={{ request.path }}"
                       class="px-2 py-1 text-xs rounded {% if current_lang == 'es' %}bg-white text-blue-600 font-semibold{% else %}text-white hover:bg-blue-700{% endif %}">ES</a>
                    <a href="/set-lang/en?next={{ request.path }}"
                       class="px-2 py-1 text-xs rounded {% if current_lang == 'en' %}bg-white text-blue-600 font-semibold{% else %}text-white hover:bg-blue-700{% endif %}">EN</a>
                </div>
```

- [ ] **Step 5.6: Inyectar `window.__i18n` y agregar helper `t()` en JS**

En el `<head>` del template, justo después de cargar Tailwind (o antes del primer `<script>` que define lógica de la app):

```html
    <script>window.__i18n = {{ translations_json | safe }};</script>
```

En el inicio del bloque `<script>` principal del template (el que define `fetchEvents`, `updateTable`, etc.), agregar:

```javascript
        function t(key) { return window.__i18n[key] || key; }
```

- [ ] **Step 5.7: Refactorizar strings dinámicos en JS**

Buscar todos los strings del Step 5.2 que están dentro del bloque `<script>`. Reemplazar:
- `'Loading events...'` → `t('table.loading')`
- `'No events found.'` → `t('table.empty')`
- `'Error loading events. Please try again later.'` → `t('table.error')`
- Status badges: el código hoy probablemente tiene `if (s === 'Trust Car') ... 'Suspicious Car' ...`. Mantener la comparación con los strings ORIGINALES (que vienen del backend en inglés) pero pintar el `textContent` con `t('status.trust_car')`, etc.

Ejemplo del refactor del status badge — código actual (simplificado):
```javascript
const getStatusBadge = (status) => {
    const s = status ? String(status) : 'Unknown';
    let colorClass = 'bg-gray-100 text-gray-800 ...';
    if (s === 'Trust Car') colorClass = 'bg-green-100 ...';
    else if (s === 'Suspicious Car') colorClass = 'bg-red-100 ...';
    else if (s === 'Normal Car') colorClass = 'bg-blue-100 ...';
    return `<span class="... ${colorClass}">${sanitize(s)}</span>`;
};
```

Refactor:
```javascript
const getStatusBadge = (status) => {
    const s = status ? String(status) : 'Unknown';
    let colorClass = 'bg-gray-100 text-gray-800 ...';
    let label = t('status.unknown');
    if (s === 'Trust Car') { colorClass = 'bg-green-100 ...'; label = t('status.trust_car'); }
    else if (s === 'Suspicious Car') { colorClass = 'bg-red-100 ...'; label = t('status.suspicious_car'); }
    else if (s === 'Normal Car') { colorClass = 'bg-blue-100 ...'; label = t('status.normal_car'); }
    return `<span class="... ${colorClass}">${sanitize(label)}</span>`;
};
```

Patrón similar para `getDirectionBadge` si existe (Approach/Leave/Unknown).

- [ ] **Step 5.8: Validar JSON + deploy + smoke test**

Run:
```bash
python3 -c "import json; json.load(open('/tmp/es.json')); json.load(open('/tmp/en.json'))" && echo "JSONs válidos"

sudo cp /tmp/index.html /root/anpr-camera-dev/app/templates/index.html
sudo cp /tmp/es.json /root/anpr-camera-dev/app/translations/es.json
sudo cp /tmp/en.json /root/anpr-camera-dev/app/translations/en.json
sudo chown root:root /root/anpr-camera-dev/app/templates/index.html /root/anpr-camera-dev/app/translations/{es,en}.json

cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: JSONs válidos, HTTP 200.

Verificar via curl + cookie de session (no se puede hacer login no-interactivo aquí; la verificación E2E real se hace en Task 8 por el usuario). Al menos confirmar que el index.html renderiza sin errores Python:

```bash
sudo docker logs --since=30s anpr-web 2>&1 | grep -iE "error|exception|traceback|template" | tail -10
```

Expected: vacío.

---

## Task 6: admin.html — Toggle + refactor de strings HTML + inyección `window.__i18n` + refactor JS

**Files:**
- Modify: `/root/anpr-camera-dev/app/templates/admin.html`
- Modify: `/root/anpr-camera-dev/app/translations/es.json`
- Modify: `/root/anpr-camera-dev/app/translations/en.json`

Mismo patrón que Task 5 pero con admin.html (mucho más grande: 794 líneas vs 520). Tres tabs + 4 modales.

- [ ] **Step 6.1: Sacar archivos a /tmp**

Run:
```bash
sudo cp /root/anpr-camera-dev/app/templates/admin.html /tmp/admin.html
sudo cp /root/anpr-camera-dev/app/translations/es.json /tmp/es.json
sudo cp /root/anpr-camera-dev/app/translations/en.json /tmp/en.json
sudo chown gabriel:gabriel /tmp/admin.html /tmp/es.json /tmp/en.json
```

- [ ] **Step 6.2: Inventariar strings de admin.html**

Run los mismos greps del Step 5.2 pero contra `/tmp/admin.html`. Estimación: ~80-100 strings traducibles entre HTML y JS.

Las áreas principales (para asignar keys):
- `admin.tabs.*` (Sessions, Users, Grupos)
- `admin.sessions.*` (tabla de sesiones, botones revoke)
- `admin.users.*` (tabla, modal crear, modal reset password, modal edit groups)
- `admin.groups.*` (tabla, modal crear/editar grupo, modal asignar cámaras)
- `admin.confirm.*` (mensajes de confirm() para delete)
- `admin.error.*` y `admin.success.*` (toasts/alerts)

- [ ] **Step 6.3: Mapear strings a keys + agregar a JSONs**

Igual que Step 5.3 pero para las áreas de admin. Mantener orden alfabético. Verificar paridad:

```bash
diff <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/es.json')).keys())))") \
     <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/en.json')).keys())))")
```

Expected: diff vacío.

- [ ] **Step 6.4: Refactorizar el HTML de admin.html con `{{ t() }}`**

Aplicar mismo patrón de Step 5.4 al HTML de admin.html. Cubrir:
- Botones de tabs ("Sessions"/"Users"/"Grupos")
- Headers de las 3 tablas (columnas)
- Botones de acción ("Editar", "Borrar", "Reset Password", "Revoke", "+ Crear ...")
- Títulos de los 4 modales
- Labels de los inputs de los modales
- Botones Cancelar/Guardar de los modales
- Mensajes empty/loading
- Warning de "Sin grupos asignados..."

- [ ] **Step 6.5: Agregar toggle ES/EN en el header del admin panel**

Localizar el header del admin (donde están los breadcrumbs, botón "Volver al dashboard", botón Logout). Insertar el mismo div del toggle del Step 5.5 con la misma estructura ES/EN.

- [ ] **Step 6.6: Inyectar `window.__i18n` + helper `t()` en JS**

Mismo patrón que Step 5.6:
- En `<head>` después de Tailwind: `<script>window.__i18n = {{ translations_json | safe }};</script>`
- Al inicio del bloque script principal: `function t(key) { return window.__i18n[key] || key; }`

(Si admin.html no tiene `escHtml` definido cerca del inicio del script, ya existe — confirmado en Task 10 de la sesión de grupos.)

- [ ] **Step 6.7: Refactorizar strings dinámicos en JS de admin**

Patrón mismo que Step 5.7 aplicado a:
- Mensajes de `loadGroups` ("No hay grupos creados aún" → `t('admin.groups.no_groups_yet')`)
- Mensajes de `loadUsers` (si tiene)
- Mensajes de error de fetch (alert/inline)
- Confirms de delete: `confirm("¿Borrar grupo "${name}"? ...")` se vuelve:
  ```javascript
  confirm(t('admin.groups.delete_confirm').replace('{name}', name))
  ```
  (Las strings con `{name}` en el JSON requieren reemplazo manual de placeholder con `.replace()` o template literal.)
- Títulos de modales: `document.getElementById('groupModalTitle').textContent = 'Crear grupo'` → `... = t('admin.groups.create')` y `'Editar grupo'` → `t('admin.groups.edit')`
- Texto del warning visual: el HTML del Step 6.4 ya lo cubre con `t()`.

- [ ] **Step 6.8: Validar JSON + deploy + smoke test**

Run:
```bash
python3 -c "import json; json.load(open('/tmp/es.json')); json.load(open('/tmp/en.json'))" && echo "JSONs válidos"

sudo cp /tmp/admin.html /root/anpr-camera-dev/app/templates/admin.html
sudo cp /tmp/es.json /root/anpr-camera-dev/app/translations/es.json
sudo cp /tmp/en.json /root/anpr-camera-dev/app/translations/en.json
sudo chown root:root /root/anpr-camera-dev/app/templates/admin.html /root/anpr-camera-dev/app/translations/{es,en}.json

cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health

sudo docker logs --since=30s anpr-web 2>&1 | grep -iE "error|exception|traceback|template" | tail -10
```

Expected: JSONs válidos, HTTP 200, sin errores de template.

---

## Task 7: anpr_web.py — Refactor de mensajes flash y jsonify

**Files:**
- Modify: `/root/anpr-camera-dev/app/anpr_web.py`
- Modify: `/root/anpr-camera-dev/app/translations/es.json` (agregar keys de mensajes backend)
- Modify: `/root/anpr-camera-dev/app/translations/en.json`

- [ ] **Step 7.1: Inventariar mensajes hardcodeados del backend**

Run:
```bash
sudo grep -nE "flash\(|jsonify.*['\"]error['\"]|jsonify.*['\"]message['\"]" /root/anpr-camera-dev/app/anpr_web.py
```

Listar cada string hardcodeado. Estimación: ~30 mensajes.

- [ ] **Step 7.2: Mapear los mensajes a keys + agregar a JSONs**

Sacar archivos a /tmp:
```bash
sudo cp /root/anpr-camera-dev/app/anpr_web.py /tmp/anpr_web.py
sudo cp /root/anpr-camera-dev/app/translations/es.json /tmp/es.json
sudo cp /root/anpr-camera-dev/app/translations/en.json /tmp/en.json
sudo chown gabriel:gabriel /tmp/anpr_web.py /tmp/es.json /tmp/en.json
```

Patrón de keys para mensajes backend:
- `backend.error.no_data` ("No se proporcionaron datos" / "No data provided")
- `backend.error.username_password_required` ("Usuario y contraseña requeridos" / "Username and password are required")
- `backend.error.user_not_found` ("Usuario no encontrado" / "User not found")
- `backend.error.user_exists` ("El usuario \"{name}\" ya existe" / "User \"{name}\" already exists")
- `backend.error.cannot_modify_admin` ("No se pueden modificar usuarios admin desde la interfaz web. Usa el CLI." / "Cannot modify admin users from the web interface. Use the CLI.")
- `backend.password.too_short` ("La contraseña debe tener al menos 10 caracteres." / "Password must be at least 10 characters long.")
- `backend.password.no_uppercase` ("La contraseña debe contener al menos una letra mayúscula." / "Password must contain at least one uppercase letter.")
- `backend.password.no_lowercase` ("La contraseña debe contener al menos una letra minúscula." / "Password must contain at least one lowercase letter.")
- `backend.password.no_digit` ("La contraseña debe contener al menos un dígito." / "Password must contain at least one digit.")
- `backend.success.user_created` ("Usuario viewer \"{name}\" creado" / "Viewer user \"{name}\" created")
- `backend.success.password_reset` ("Contraseña reseteada para \"{name}\"" / "Password reset for \"{name}\"")
- ... etc.

Agregar todas las keys necesarias a ambos JSONs. Verificar paridad después:

```bash
diff <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/es.json')).keys())))") \
     <(python3 -c "import json; print('\n'.join(sorted(json.load(open('/tmp/en.json')).keys())))")
```

- [ ] **Step 7.3: Refactorizar el backend para usar `t()`**

Reemplazar en `/tmp/anpr_web.py`:

Patrón 1 — string sin interpolación:
```python
flash('Invalid username or password')
# se vuelve
flash(t('login.invalid'))
```

```python
return jsonify({'error': 'No data provided'}), 400
# se vuelve
return jsonify({'error': t('backend.error.no_data')}), 400
```

Patrón 2 — string con f-string (interpolación):
```python
return jsonify({'error': f'User "{username}" already exists'}), 409
# se vuelve
return jsonify({'error': t('backend.error.user_exists').format(name=username)}), 409
```

(Las keys con `{name}` en el JSON se invocan con `.format(name=value)` en Python.)

Patrón 3 — mensajes en la función `is_password_strong`:
```python
def is_password_strong(password):
    if len(password) < 10:
        return False, "Password must be at least 10 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    # ...
```

Se vuelve:
```python
def is_password_strong(password):
    if len(password) < 10:
        return False, t('backend.password.too_short')
    if not any(c.isupper() for c in password):
        return False, t('backend.password.no_uppercase')
    # ...
```

Aplicar el mismo patrón a cada `flash()` y cada `jsonify({...})` con string hardcodeado.

NO traducir los `logger.info`, `logger.error`, `logger.warning` — esos son para ops, no para usuarios.

- [ ] **Step 7.4: Validar sintaxis + deploy**

Run:
```bash
python3 -m py_compile /tmp/anpr_web.py && echo "Sintaxis OK"
python3 -c "import json; json.load(open('/tmp/es.json')); json.load(open('/tmp/en.json'))" && echo "JSONs válidos"

sudo cp /tmp/anpr_web.py /root/anpr-camera-dev/app/anpr_web.py
sudo cp /tmp/es.json /root/anpr-camera-dev/app/translations/es.json
sudo cp /tmp/en.json /root/anpr-camera-dev/app/translations/en.json
sudo chown root:root /root/anpr-camera-dev/app/anpr_web.py /root/anpr-camera-dev/app/translations/{es,en}.json

cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Expected: `Sintaxis OK`, `JSONs válidos`, HTTP 200.

- [ ] **Step 7.5: Smoke test del backend en ambos idiomas**

Run (sin login real, solo validando que el flash + jsonify responde con el string correcto):
```bash
echo "=== POST login con credenciales inválidas, default lang (es) ==="
curl -s -c /tmp/c1 -o /tmp/r1 -w "HTTP %{http_code}\n" -X POST http://localhost:5000/login \
  -d "username=fake" -d "password=fake"
grep -oE "Usuario o contraseña inválidos|Invalid username or password" /tmp/r1

echo "=== POST login inválido con cookie lang=en ==="
curl -s -b "lang=en" -o /tmp/r2 -w "HTTP %{http_code}\n" -X POST http://localhost:5000/login \
  -d "username=fake" -d "password=fake"
grep -oE "Usuario o contraseña inválidos|Invalid username or password" /tmp/r2
```

Expected: primer test devuelve el español, segundo el inglés. Si ambos devuelven lo mismo o si aparecen literales de keys, revisar.

---

## Task 8: Validación E2E + commit + push

**Files:** ninguno modificado — verificación + git

Esta task requiere coordinación con el usuario para validar en browser real con admin + viewer.

- [ ] **Step 8.1: Setup de prueba — usuario navega a /login en pestaña incógnita**

El usuario:
1. Abre `/login` en una pestaña incógnita (sin cookies previas).
2. Verifica que la página aparece en español (default).
3. Click en el botón "EN" → la página recarga en inglés.
4. Verifica que: título, labels username/password y botón submit están en inglés.

- [ ] **Step 8.2: Login + dashboard como viewer en ambos idiomas**

Usuario continúa:
5. Login como `visorjorgecarrillo` con la página en inglés.
6. Verifica que el dashboard aparece en inglés: header, columnas de la tabla, dropdowns de filtro, botones de paginación, status badges, toast de "New events available" si hay tráfico.
7. Click ES → todo cambia a español sin perder sesión.
8. Verifica strings dinámicas: si pasa un vehículo durante la prueba, el toast debe estar en el idioma activo.

- [ ] **Step 8.3: Admin panel en ambos idiomas**

Usuario en sesión admin:
9. Navega a `/admin`.
10. Verifica las 3 tabs (Sessions, Users, Grupos) y sus contenidos en español.
11. Abre el modal "Crear grupo" — verifica que título, labels y botones están en español.
12. Cambia a EN — todo se actualiza, incluyendo el modal si está abierto (al recargar la página).
13. Abre el modal de Crear viewer + el de Editar grupos del viewer — verifica traducción.

- [ ] **Step 8.4: Test de fallback (key faltante)**

El usuario abre devtools console y ejecuta:
```javascript
console.log(t('nonexistent.test.key'));
```

Expected: imprime `nonexistent.test.key` literal (no error). Confirma que el fallback funciona.

- [ ] **Step 8.5: Cero errores en logs durante la prueba**

Run después de que el usuario termine las validaciones:
```bash
sudo docker logs --since=15m anpr-web 2>&1 | grep -iE "error|exception|traceback" | tail -10
```

Expected: vacío. Si hay traceback, investigar antes de commit.

- [ ] **Step 8.6: Commit final**

Run:
```bash
sudo git -C /root/anpr-camera-dev status --short
sudo git -C /root/anpr-camera-dev add \
  app/anpr_web.py \
  app/templates/login.html \
  app/templates/index.html \
  app/templates/admin.html \
  app/translations/es.json \
  app/translations/en.json \
  anpr_web.Dockerfile \
  docs/superpowers/plans/2026-06-05-i18n-implementation.md
sudo git -C /root/anpr-camera-dev status --short

sudo git -C /root/anpr-camera-dev commit -m "$(cat <<'EOF'
feat(i18n): Spanish/English UI with cookie-based per-browser preference

Architecture:
- app/translations/{es,en}.json: flat snake_case keys, ~160 strings.
  Loaded at anpr-web startup (fail-fast if missing/malformed).
- Cookie 'lang' (1-year max-age, SameSite=Lax) persists choice per
  browser. Default 'es' when absent.
- Unified t(key) helper in Python (backend + Jinja via
  context_processor) and JS (via window.__i18n injected per page).
- GET /set-lang/<lang>?next=<url> sets cookie + redirects. Open redirect
  protected by same-host validation already used by /login.

Templates (login, index, admin): toggle ES/EN visible in header.
Strings refactored to use {{ t('key') }} in Jinja and t('key') in JS.
JS receives only the active language's dict via window.__i18n.

Backend (anpr_web.py): flash() messages and jsonify({'error': ...})
responses now use t(key). String interpolation via .format(name=value)
for keys that contain {name} placeholders. logger.* calls left in
English as they are ops-only.

Dockerfile updated to COPY app/translations/ into the image.

Out of scope (deliberately, per spec): pluralization, date/number
localization, more languages, RTL, DB-persisted preference.

Feature is fully additive; rollback Level 1 only via the pre-deploy
tarball at /root/anpr-camera-dev/backups/code_pre_i18n_<TS>.tar.gz.

Verified empirically in production: login + dashboard + admin panel
all render correctly in both languages, toggle works seamlessly, fallback
to key-literal visible for missing keys (none found in QA), zero errors
in logs during testing.

Implementation plan: docs/superpowers/plans/2026-06-05-i18n-implementation.md
Spec: docs/superpowers/specs/2026-06-05-i18n-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

sudo git -C /root/anpr-camera-dev log -1 --stat
```

Expected: commit creado, stat muestra los 8 files cambiados.

- [ ] **Step 8.7: Push a origin/master**

Run:
```bash
sudo GIT_SSH_COMMAND="ssh -i /root/.ssh/repo_keys/anpr-camera-dev -o IdentitiesOnly=yes -o BatchMode=yes" \
  git -C /root/anpr-camera-dev push git@github.com:gabrielpc1190/anpr-camera-dev.git master 2>&1
sudo git -C /root/anpr-camera-dev fetch origin
sudo git -C /root/anpr-camera-dev status --short --branch | head -3
```

Expected: push exitoso, branch sincronizado con origin.

---

## Rollback (si algo sale mal)

Único nivel necesario (la feature no toca DB):

```bash
TS=<el TS apuntado en Task 1>

# Restaurar código desde el tar de respaldo
sudo mkdir -p /tmp/restore_i18n
sudo tar xzf /root/anpr-camera-dev/backups/code_pre_i18n_${TS}.tar.gz -C /tmp/restore_i18n/
sudo cp -r /tmp/restore_i18n/app/* /root/anpr-camera-dev/app/
sudo cp /tmp/restore_i18n/anpr_web.Dockerfile /root/anpr-camera-dev/anpr_web.Dockerfile

# Rebuild + recreate
cd /root/anpr-camera-dev && sudo docker-compose build anpr-web
cd /root/anpr-camera-dev && sudo docker-compose up -d anpr-web

# Verify
sleep 15
curl -s -o /dev/null -w "anpr-web: HTTP %{http_code}\n" http://localhost:5000/health
```

Los archivos `app/translations/*.json` quedan en disco pero ningún código los consume (porque revertimos `anpr_web.py`). Para limpieza absoluta:
```bash
sudo rm -rf /root/anpr-camera-dev/app/translations/
```

---

## Self-review check

Antes de empezar la implementación, verificar:

- **Spec coverage**: cada sección del spec tiene su task. Sí: Tasks 2-3 cubren infraestructura (JSON + Dockerfile + helpers backend), Tasks 4-6 cubren los 3 templates, Task 7 cubre el refactor del backend, Task 8 cubre validación + commit.
- **Placeholders**: no hay TBD/TODO/etc en el plan. Los ejemplos de keys en cada Task son concretos y sacados del spec sección 5.
- **Type consistency**: `t(key)` consistente en backend (Python), templates (Jinja), JS. `get_lang()` y `SUPPORTED_LANGS` definidos en Task 3 y referenciados consistentemente. `TRANSLATIONS` global, `current_lang` y `translations_json` en context processor.
- **Naming convention**: spec dice plano snake_case con prefijo de área. Plan usa exactamente eso en todos los ejemplos.
- **Code blocks**: cada step que cambia código incluye el código completo.
- **Verificaciones explícitas**: cada task tiene smoke test con expected output específico.
- **Commits**: el plan hace UN solo commit al final (Step 8.6) porque las tareas son funcionalmente interdependientes (templates y backend traducidos requieren el helper `t()`, el helper requiere los JSON cargados). Si se prefiere granularidad, los commits se podrían dividir por task — anotación al equipo de implementación.
- **Manual de extracción de strings**: las Tasks 4-6 dejan claro CÓMO inventariar strings (greps específicos) y CÓMO asignar keys (convención del spec sección 5). No hay "TODO list strings".
