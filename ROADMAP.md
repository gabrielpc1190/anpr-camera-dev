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

### Phase 4: Session & Security Refinement (v2.2)
- Advanced Session identification and protection.
- "Revoke All" functionality (preserving current session).
- Robust Password Complexity (10+ chars, mixed case, digits).
- UI security hints and frontend validation.
- Legacy role migration removal.

## 🔜 Future Vision

### Near Term
- [ ] Email/Telegram notifications for specific license plate alerts.
- [ ] Real-time Dashboard updates via WebSockets.
- [ ] Log rotation and automatic disk cleanup for images.

### Long Term
- [ ] Multi-tenant support for different sites.
- [ ] AI-driven vehicle color/brand recognition.
- [ ] Mobile-native application interface.
