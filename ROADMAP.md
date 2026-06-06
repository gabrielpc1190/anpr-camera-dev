# Project Roadmap - ANPR Camera System

## ✅ Completed Milestones

### Phase 1: Core System (v1.0)
- Real-time ANPR event capture from Dahua cameras.
- Asynchronous database management (MariaDB).
- Basic Dashboard for event viewing.
- Dockerized microservices architecture.

### Phase 2: User Authentication & Roles (v2.0)
- Secure login system with bcrypt hashing.
- Role-based access control (Admin vs. Viewer).
- Admin management via CLI tools.
- Session-based security for web routes.

### Phase 3: Performance & UI Enhancement (v2.1)
- GLightbox integration for license plate images.
- Gunicorn multi-threaded architecture (Handle 4+ cameras).
- Hotkey shortcuts for rapid navigation.
- Dashboard optimizations for high event load.

### Phase 4: Session & Security Refinement (v2.2 & v2.3)
- Advanced Session identification and protection.
- "Revoke All" functionality (preserving current session).
- Robust Password Complexity (10+ chars, mixed case, digits).
- **Session IP Tracking** (Backend capture + UI display).
- UI security hints and frontend validation.
- Legacy role migration removal.

### Phase 5: Camera Identity Refactor (v2.4)
- **Per-camera callback closures** (`make_analyzer_callback` factory): each camera gets a dedicated ctypes callback bound by closure, eliminating event mis-attribution when cameras share an external IP via NAT/port-forwarding.
- **`cameras` table**: new MariaDB table (id INT PK, friendly_name, ip_address, port) synced from `config.ini` at db-manager startup.
- **`camera_id` INT FK** in `anpr_events`: `camera_id` column renamed to `camera_friendly_name` (preserves history); new `camera_id INT NULL FK → cameras.id` added with index `idx_camera_id` and constraint `fk_anpr_events_camera`. 115 461 historical rows backfilled via JOIN.
- **API updated**: `GET /api/cameras` now reads from the `cameras` table; `GET /api/events` returns both `camera_id` (INT) and `camera_friendly_name` (string); `POST /event` writes both columns with FK validation.
- **UI updated**: camera dropdown value is integer `cam.id`; event rows display `camera_friendly_name`.
- **`Id` field required** in `[Camera.X]` config sections; listener validates it as integer at startup and skips misconfigured cameras with a clear error log.
- Resolves production bug: two Dahua cameras behind shared NAT IP (10.49.9.50, ports 1177/1277) were mis-attributed because the SDK does not reliably distinguish subscriptions by handle when cameras share an external IP.

### Phase 7: JXL Lossless Image Compression — Part 1 (v2.7, 2026-06-06)
- **Backend serves JXL transparently**: `serve_image()` in `anpr_web.py` falls back to a sibling `.jxl` (decoded on-the-fly via `djxl`) when the `.jpg` is missing on disk. Endpoint contract unchanged — clients still request `/images/<name>.jpg`. `werkzeug.utils.safe_join` guards against path traversal.
- **`libjxl-tools` in `anpr_web.Dockerfile`**: ships `cjxl`/`djxl` into the container.
- **Historical sweep**: `scripts/transcode_jxl.py --replace` (already in repo, fully tested) processes the corpus (~128 k JPEGs from cameras emitting Q=71). JPEG XL **lossless transcode** (`cjxl -d 0 -j 1`) reorganizes DCT coefficients into a denser container; `djxl` reconstructs the original JPEG byte-for-byte (SHA-256 verified). Empirical savings ~17 % on this corpus.
- **Result**: one-time disk reclaim ~20 GB (corpus 119 GB → ~99 GB). Cameras still write `.jpg` directly — Part 2 below addresses ongoing ingestion.
- See `docs/superpowers/specs/2026-06-05-jxl-compression-design.md`, `docs/superpowers/plans/2026-06-05-jxl-compression-phase1.md`, `docs/deployment/jxl-transcode-runbook.md`.

## 🔍 Open Field Investigations

See [`docs/field-investigations.md`](docs/field-investigations.md) for active anomalies that need on-site or in-camera-UI verification (not code changes):

- **CAM3 vs CAM4 detection asymmetry (Fase 5)** — CAM4 logs ~2.24× more events than CAM3. Ruled out as SDK/network issue; likely IVS/ROI/physical-encuadre difference. Verify in camera UI next session.
- **`confidence = 0` in all Fase 5 plate events** — listener reads `nConfidence` from SDK, but Fase 5 cameras don't populate it (possibly firmware-related). Cosmetic but worth understanding.

## 🔜 Future Vision

### Near Term
- [ ] **⚠️ Phase 7 Part 2: Auto-compression of new ingestion (JXL watcher + admin toggle)** — **important pending**. Phase 7 Part 1 compressed the historical corpus one-time (~20 GB recovered) but cameras still write `.jpg` directly, so the disk fills again at ~745 MB/day. Without this part, the saved space is consumed in ~27 days. Spec already exists (`docs/superpowers/specs/2026-06-05-jxl-compression-design.md`, sections B and C). Pending:
  - New table `app_settings` (idempotent `CREATE TABLE IF NOT EXISTS`) with `compress_new_images` boolean.
  - `GET`/`PUT /api/admin/settings/<key>` endpoints (admin-only, mirrored from db-manager to anpr-web following the camera-groups pattern).
  - New "Configuración" tab in admin panel with the toggle (i18n strings under `admin.settings.*`).
  - Refactor: extract transcode logic from `scripts/transcode_jxl.py` into shared module `app/jxl_transcode.py`.
  - New Docker service `anpr-jxl-watcher`: Python + `inotify_simple` + 4-worker thread pool, reads the toggle from DB (cached 30 s), processes new `.jpg` files via `IN_CLOSE_WRITE`. Healthcheck via touch-file. Initial sweep on startup catches missed files.
  - Expected steady-state result: growth drops from ~745 to ~620 MB/day (-17 %), making local storage sustainable until Phase 6 (Backblaze tiered) is ready.
- [ ] **Plate watchlist with Telegram notifications**: maintain a list of "plates of interest" (managed via admin UI or DB). When the listener detects a plate that matches an entry in the list, send a Telegram message in real time with the plate, camera, timestamp, and image. Includes deduplication (same plate in same camera within N seconds is one notification), admin management of the list, and an audit log of notifications sent.
- [ ] Real-time Dashboard updates via WebSockets.
- [ ] **Phase 6: Tiered image retention with Backblaze B2 archival**.
  Disk pressure is real: `app/anpr_images/` grows ~14 GB/month and would exhaust the 196 GB disk in ~4 months at current rate. Design (to be implemented soon):
  - **Tier 1 (months 0-12)**: keep images on local disk in original quality. Steady-state ~168 GB.
  - **Tier 2 (months 13-24)**: archive to Backblaze B2 (cloud object storage). Local file is removed after successful upload; DB row keeps `image_filename` plus a new column indicating remote location.
  - **Tier 3 (>24 months)**: definitive deletion (subject to confirmation when implementing).
  - Cleanup/archival runs as a scheduled job (cron or container service, TBD during spec).
  - DB rows in `anpr_events` are preserved across all tiers — only the binary image moves/disappears. Dashboard fetches old images transparently from B2 when needed.
- [ ] Log rotation for application logs (`anpr_listener.log`, `anpr_db_manager.log`).

### Long Term
- [ ] Multi-tenant support for different sites.
- [ ] AI-driven vehicle color/brand recognition.
- [ ] Mobile-native application interface.
