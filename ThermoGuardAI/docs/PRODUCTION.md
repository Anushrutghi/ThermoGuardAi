# Production — Setup, Environment & Operations (S5)

This document describes how to run ThermoGuard AI in production. It documents
**only behavior that actually exists** — nothing here claims hardware or
mobile validation that was not physically performed.

## 1. Deployment architecture

```
Browser (React SPA / Streamlit / mobile)
   │  HTTPS + WSS      │  Firebase Authentication (ID tokens)
   ▼                   ▼
FastAPI backend  ──►  Cloud Firestore (all application data)
   │                  Firebase Storage (report PDFs)
   └── Thermal subsystem: auto-detects a real sensor, else DEMO simulator
```

- **Frontend**: the React dashboard is a static SPA (`frontend/react/dist`)
  served by the backend or any static host (CORS must list its origin). Users
  authenticate with **Firebase Authentication** (email/password or Google);
  the Firebase ID token is exchanged at `POST /api/v1/auth/firebase` for the
  application session token. `VITE_FIREBASE_*` public web config is required
  in the production build.
- **Backend**: FastAPI + Uvicorn. WebSocket live inspection uses `ws://` /
  `wss://`. Identity is Firebase (`firebase_admin.auth.verify_id_token`); role
  and organization come **only** from the Firestore `users/{uid}` profile.
- **Database**: Cloud Firestore (`STORAGE_BACKEND=firestore`) — all
  application data (devices, panels, components, inspections, detections,
  faults, thermal history, reports metadata, maintenance, alarms, events).
  SQLite/PostgreSQL are never used in production.
- **Storage**: report PDFs live in **Firebase Storage** (`reports/{id}.pdf`);
  download streams through the authorized endpoint — no public URLs, no
  filesystem paths. Captured media frames stay under `media/frames`.
- **No local database is created at startup.** `init_db()`/seeding are skipped
  in firestore mode; `SessionLocal()`/the SQLAlchemy engine raise if any code
  path tries to touch them.

## 1b. Firebase provisioning checklist (Phase 2.1 — current status)

| Service | Required | Current status (audited) | Action needed |
| --- | --- | --- | --- |
| Firebase project `thermoguardai` | ✅ | Exists; Admin SDK initializes | — |
| Firebase Authentication | ✅ | Admin SDK `list_users` works (0 users) | Enable Email/Password + Google (+ Phone) in console; add authorized domains |
| **Cloud Firestore** | ✅ | ❌ **API DISABLED (403 PermissionDenied)** | **Console: enable Cloud Firestore (production mode)** |
| **Firebase Storage bucket** | ✅ | ❌ **Bucket does not exist (404)** | **Console: create `thermoguardai.firebasestorage.app`; deploy `storage.rules`** |
| Firestore/Storage rules | ✅ | `firestore.rules` + `storage.rules` in repo (deny-all direct client) | `firebase deploy --only firestore:rules,storage` |
| Service-account JSON | ✅ | Present at `secrets/…` (gitignored) | Keep backend-only |
| React web-app config | ✅ | Public `VITE_FIREBASE_*` values available | Build React with them |

## 2. Environment variables

Copy `.env.example` to `.env` and set every production value:

| Variable | Production value | Notes |
| --- | --- | --- |
| `APP_ENV` | `production` | Refuses to start with unsafe settings |
| `SECRET_KEY` | random ≥ 32 chars | JWT signing; must never be committed |
| `DEBUG` | `false` | Startup fails if true in production |
| `CORS_ORIGINS` | explicit origins | `*` is rejected in production |
| `ALLOWED_HOSTS` | your hostnames | TrustedHostMiddleware (no ports) |
| `AUTH_BACKEND` | `firebase` | Required in production (startup fails otherwise) |
| `STORAGE_BACKEND` | `firestore` | Required in production (startup fails otherwise) |
| `VITE_AUTH_MODE` (React build) | unset | Leave unset in production; `local` ONLY for local dev (enables legacy login) |
| `FIREBASE_PROJECT_ID` | `thermoguardai` | Backend Firebase project |
| `FIREBASE_STORAGE_BUCKET` | `thermoguardai.firebasestorage.app` | Report file bucket |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to service-account JSON | Backend-only secret |
| `THERMAL_MODE` | `auto` (or a driver id) | `auto` probes real sensors first |
| `LOG_LEVEL` | `INFO` | Never logs secrets |
| `JWT_EXPIRE_MINUTES` | e.g. `480` | Access-token lifetime |

**Never** set in production: weak/default `SECRET_KEY`, `CORS_ORIGINS=*`,
`DEBUG=true`, test credentials.

## 3. Production safety gates (enforced at startup)

`Settings.validate_security()` aborts startup when `APP_ENV=production` and any
of: `SECRET_KEY` is the default or < 32 chars, `CORS_ORIGINS` contains `*` or
is empty, `ALLOWED_HOSTS` is empty, or `DEBUG=true`. The `SECRET_KEY` default
also logs a warning in development.

## 4. Database (Cloud Firestore)

- **Production persistence is Cloud Firestore.** With
  `STORAGE_BACKEND=firestore` the local database is never created, migrated
  or seeded: `init_db()` and seeding are skipped at startup, and any code
  path that touches `SessionLocal()` or the SQLAlchemy engine fails loudly
  (it can never silently fall back to SQLite).
- **Firestore schema:** `users/{uid}` profiles + `usernames/{username}`
  lookups + `organizations/{orgKey}`; domain entities in flat collections
  named after the ORM tables (`devices`, `panels`, `components`,
  `inspections`, `detections`, `faults`, `incidents`, `alarms`,
  `maintenance_records`, `thermal_history`, `temperature_history`, `reports`)
  with integer IDs allocated from the `__counters` collection and
  organization isolation via a derived `organization` field. See
  `docs/S6_FIREBASE_PLAN.md` for the mapping.
- **Local SQLite is development/test only** (isolated
  `database/test_thermoguard.db`). Tests never touch production data.
- Backup: Firebase handles durability; export Firestore via the console and
  keep the service-account credential safe.
- **Media frames:** inspection evidence screenshots are written to a local
  `media/frames/` directory as **temporary working copies** used to embed
  images into generated PDFs. This is NOT production persistence (the PDF is
  the persisted artifact, in Firebase Storage). A follow-up migration to
  store evidence frames in Firebase Storage (`inspections/{id}/…`) with
  controlled sampling is recommended — it was **not** auto-implemented
  because it changes storage semantics and requires approval.

## 5. Thermal subsystem

- `THERMAL_MODE=auto` probes `MLX90640 → Lepton → AMG8833 → Seek` in order and
  selects the first physically available driver.
- If no real sensor is detected the **DEMO simulator** is used; it is always
  labelled `simulated=true` on inspections, thermal-history rows and reports.
- The backend is the **single authority** on REAL vs DEMO:
  `GET /api/v1/thermal/status` returns `hardware`, `lifecycle`, `quality`,
  `measurement` (MEASURED/SIMULATED/UNAVAILABLE/INVALID), `metadata`,
  `emissivity` and `ambient_c`. See `docs/THERMAL_HARDWARE.md`.

## 6. Health checks

| Endpoint | Meaning |
| --- | --- |
| `GET /health/live` | API process is up (liveness) |
| `GET /health/ready` | Database round-trip succeeds (readiness); 503 otherwise |
| `GET /health` | Aggregate: app + database + thermal subsystem state |
| `GET /api/v1/thermal/status` | Authoritative thermal hardware status (auth) |

`/health` reports `thermal_health` = `ok` only when a real sensor is producing
MEASURED data; DEMO/simulated or unavailable is `degraded`. The application
never claims "fully healthy" merely because the API process is up.

## 7. Simulator mode

Run with `THERMAL_MODE=simulator` to force DEMO. Every simulated reading is
labelled DEMO/SIMULATED in the UI and in data; simulated thermal-history rows
are excluded from real analytics by default (`exclude_demo=true`) and counted
separately (`simulated_excluded`).

## 8. HTTPS & secure context

Browser camera access (Live Inspection) **requires a secure context** — the
site must be served over HTTPS (or localhost). The same requirement applies to
the WebSocket `wss://` channel. Deploy behind a TLS-terminating proxy
(Traefik/Caddy/nginx) with `X-Forwarded-*` headers configured.

## 9. Secrets & logging

- Passwords are hashed (bcrypt); JWT secrets and API keys never appear in
  logs or API responses.
- Structured logging covers authentication failures, inspection
  start/stop/abort, thermal connection/rejection, report generation and WS
  connect/disconnect — with `inspection_id` / `device_id` correlation where
  applicable, and never raw tokens or full frames.
