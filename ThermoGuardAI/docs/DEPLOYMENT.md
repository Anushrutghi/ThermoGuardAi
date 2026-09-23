# Deployment Guide (S5)

How to deploy ThermoGuard AI to production. **The hosted site MUST use HTTPS**
because browser camera access (Live Inspection) requires a secure context, and
the live WebSocket must be `wss://`.

## 1. Prerequisites

- Python 3.12+ and Node 18+ (build host).
- A TLS-terminating reverse proxy (Caddy, Traefik, or nginx) in front of the
  backend.
- A Firebase project (`thermoguardai`) with **Cloud Firestore enabled**, the
  **Storage bucket** created, and **Authentication** (Email/Password +
  Google) configured.
- The Firebase Admin SDK **service-account JSON** (backend-only secret).
- A persistent volume for `media/` and `reports/generated/` (frames).

Production persistence is **Cloud Firestore + Firebase Storage + Firebase
Authentication** — no SQLite, no PostgreSQL. Production startup fails unless
`AUTH_BACKEND=firebase` and `STORAGE_BACKEND=firestore` are configured.

## 2. Build

```bash
# backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# frontend
cd frontend/react && npm ci && npm run build   # outputs dist/
```

## 3. Configure

1. `cp .env.example .env` and set every variable from `docs/PRODUCTION.md` §2
   (`APP_ENV=production`, `AUTH_BACKEND=firebase`, `STORAGE_BACKEND=firestore`,
   `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`,
   `GOOGLE_APPLICATION_CREDENTIALS`, strong `SECRET_KEY`, explicit
   `CORS_ORIGINS`, `ALLOWED_HOSTS`, `DEBUG=false`).
2. Build the React SPA with the public Firebase web config:
   `cd frontend/react && VITE_FIREBASE_API_KEY=... VITE_FIREBASE_AUTH_DOMAIN=... \
   VITE_FIREBASE_PROJECT_ID=... VITE_FIREBASE_STORAGE_BUCKET=... \
   VITE_FIREBASE_MESSAGING_SENDER_ID=... VITE_FIREBASE_APP_ID=... npm run build`.
   These are public web-app values (not secrets) and must match the Firebase
   project.
3. Start the backend: `uvicorn backend.main:app --host 0.0.0.0 --port 8000`
   (or the provided `scripts/start.sh` / Docker image in `docker/`). On first
   run the seed admin is bootstrapped in Firebase Auth + Firestore from
   `SEED_ADMIN_*`.

Startup fails fast on unsafe production configuration (including any missing
Firebase setup) and never falls back to a local database.

## 4. HTTPS + WebSocket

| Requirement | How |
| --- | --- |
| HTTPS for camera access | TLS termination at the proxy; SPA served over https |
| WSS for live inspection | Proxy upgrade `Upgrade: websocket` → backend `8000` |
| Trusted host | `ALLOWED_HOSTS` lists the public hostname |
| CORS | `CORS_ORIGINS` lists the SPA origin(s) |

Example Caddy:

```
thermoguard.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

## 5. Database (Firestore) & Storage

- **Cloud Firestore** holds all application data; the backend is the only
  writer (Admin SDK). No local database is created at startup and no
  SQLite/PostgreSQL service is required.
- **Firebase Storage** holds generated report PDFs under `reports/{id}.pdf`.
  The download endpoint authorizes per-organization, then streams the blob —
  no public or signed URLs, no filesystem paths in API responses.
- Backup: use the Firebase console's Firestore export, and never lose the
  service-account credential. Media frames on the persistent volume should be
  backed up with the reports directory.
- Data safety: existing Firestore data is never deleted, reset, overwritten
  or restructured by this deployment.

## 6. Verification after deploy

1. `GET /health/live` → `ok`
2. `GET /health/ready` → `ok` (database round-trip)
3. `GET /health` → app/db/thermal status; `thermal_health` reflects an actual
   sensor, not the API process
4. `GET /api/v1/thermal/status` (authenticated) → authoritative hardware
   state (REAL SENSOR or DEMO / SIMULATED THERMAL)
5. Login → create device → start a live inspection from a browser over HTTPS
   (camera permission prompt appears)
6. Generate + download a report

## 7. Production environment checklist

- [ ] `APP_ENV=production`, `AUTH_BACKEND=firebase`, `STORAGE_BACKEND=firestore`, `DEBUG=false`
- [ ] `SECRET_KEY` ≥ 32 random chars (never committed)
- [ ] `FIREBASE_PROJECT_ID` = `thermoguardai`, Storage bucket configured, Firestore enabled
- [ ] `GOOGLE_APPLICATION_CREDENTIALS` points at the backend-only service-account JSON
- [ ] React SPA built with `VITE_FIREBASE_*` web config; Firebase Auth login verified
- [ ] `CORS_ORIGINS` explicit (no `*`), `ALLOWED_HOSTS` set
- [ ] HTTPS + `wss://` confirmed working from the browser
- [ ] Media/reports on persistent storage, backups scheduled
- [ ] Real thermal sensor configured via `THERMAL_MODE` (or DEMO clearly
      labelled)
- [ ] Health checks wired into your monitoring
- [ ] No secrets in the repository (`.env` ignored, service account gitignored)

## 8. Browser support

Camera live inspection targets desktop Chrome/Edge/Safari and Android
Chrome/iOS Safari. Physical mobile-device QA was **not** performed in this
environment — desktop secure-context behaviour is verified; device-specific
permission flows are documented but unverified on real mobile hardware.
