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

## 🔜 Future Vision

### Near Term
- [ ] Email/Telegram notifications for specific license plate alerts.
- [ ] Real-time Dashboard updates via WebSockets.
- [ ] Log rotation and automatic disk cleanup for images.

### Long Term
- [ ] Multi-tenant support for different sites.
- [ ] AI-driven vehicle color/brand recognition.
- [ ] Mobile-native application interface.
