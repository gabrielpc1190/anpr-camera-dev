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

## 🔍 Open Field Investigations

See [`docs/field-investigations.md`](docs/field-investigations.md) for active anomalies that need on-site or in-camera-UI verification (not code changes):

- **CAM3 vs CAM4 detection asymmetry (Fase 5)** — CAM4 logs ~2.24× more events than CAM3. Ruled out as SDK/network issue; likely IVS/ROI/physical-encuadre difference. Verify in camera UI next session.
- **`confidence = 0` in all Fase 5 plate events** — listener reads `nConfidence` from SDK, but Fase 5 cameras don't populate it (possibly firmware-related). Cosmetic but worth understanding.

## 🔜 Future Vision

### Near Term
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
