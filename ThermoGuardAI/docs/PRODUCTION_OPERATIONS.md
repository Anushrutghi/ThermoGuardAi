# Production Operations (S4)

Deployment, security configuration, HTTPS/WebSocket requirements, health
checks, and backup/recovery for ThermoGuard AI.

---

## 1. Production configuration checklist

1. `APP_ENV=production` — the backend **refuses to start** on unsafe config:
   - `SECRET_KEY` < 32 chars or still the default → startup error.
   - `CORS_ORIGINS` containing `*` (credentials are enabled) → startup error.
   - `CORS_ORIGINS` empty → startup error.
   - `ALLOWED_HOSTS` empty → startup error.
   - `DEBUG=true` → startup error.
2. Generate the secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Set explicit `CORS_ORIGINS` (comma-separated https origins of your UIs).
4. Set `ALLOWED_HOSTS` to your real hostnames (no scheme/port).
5. Set `AUTH_BACKEND=firebase` and `STORAGE_BACKEND=firestore` — production
   refuses to start otherwise (Firebase Authentication + Cloud Firestore +
   Firebase Storage; **no** PostgreSQL/SQLite in production).
6. Set `FIREBASE_PROJECT_ID=thermoguardai`, `FIREBASE_STORAGE_BUCKET` and
   `GOOGLE_APPLICATION_CREDENTIALS` (backend-only service-account JSON).
7. Build the React SPA with the public `VITE_FIREBASE_*` web config so users
   can authenticate with Firebase.
8. Override `SEED_ADMIN_PASSWORD` (the dev default is documented but must not
   ship to production).
9. Never commit `.env` or the service account (gitignored); see `.env.example`
   for the full template.

## 2. Environment tiers

| Tier | APP_ENV | CORS | TrustedHost middleware | Security validation |
| --- | --- | --- | --- | --- |
| development | `development` | localhost origins | off | warnings only |
| staging | `staging` | explicit staging origins | on | enforced |
| production | `production` | explicit production origins | on | enforced (fatal) |

## 3. HTTPS — required (browser camera)

`getUserMedia` (browser camera) requires a **secure context**. The hosted
production site **must** use HTTPS on both the dashboard and the API/WebSocket
origin. Without HTTPS the camera permission prompt will not be offered by the
browser and Live Inspection cannot work.

- Terminate TLS at your reverse proxy (Caddy/nginx/Traefik/Cloudflare).
- Serve the dashboard and `/api/v1` + `/api/v1/ws/inspect` behind the same
  HTTPS origin (the client builds `wss://` automatically when the page is
  served over HTTPS).

## 4. WebSocket deployment requirements

- The inspection channel is `wss://host/api/v1/ws/inspect?token=<JWT>`.
- Proxies must support **WebSocket upgrades** (Connection: Upgrade /
  Upgrade: websocket), no aggressive idle timeouts (inspections stream
  continuously; clients also send `ping` heartbeats).
- Auth happens **before** `accept()`; unauthenticated connections are closed
  with code 4401.
- One connection runs at most one inspection; duplicate starts are rejected.
- On disconnect, only a *running* inspection is aborted — a gracefully stopped
  inspection stays completed.

## 5. Authentication hardening notes

- Passwords: bcrypt (`hash_password` / `verify_password`).
- JWT: HS256, `iat`/`exp` claims, `jwt_expire_minutes` configurable (8h).
- Malformed/expired tokens and missing/foreign claims are rejected uniformly
  (HTTP 401, WS close 4401). No refresh-token flow exists — clients re-login.
- Secrets are never logged; auth failures log the username only.
- Frontends keep the token in memory/localStorage; report download URLs may
  carry `?token=` (accepted alongside the `Authorization` header) — the
  download response is `Cache-Control: no-store` so tokens are not cached.

## 6. Health endpoints

| Endpoint | Meaning |
| --- | --- |
| `GET /health/live` | Process is up (liveness). |
| `GET /health/ready` | Real `SELECT 1` round-trip to the database (readiness; 503 on failure). |
| `GET /health` | Aggregate: app, `database_ok`, detector engine, thermal source, `thermal_simulated`, `thermal_lifecycle`, `thermal_quality`, metadata. |

The aggregate never claims "fully healthy" just because the process runs: DB
failure degrades it to `"status": "degraded"` and thermal availability is
reported honestly (DEMO labelled).

## 7. Backup / recovery

- **Database (production)**: all application data lives in **Cloud Firestore**
  (project `thermoguardai`). Use the Firebase console's Firestore export or
  a scheduled backup. Local SQLite (`database/thermoguard.db`) is a
  development/test artifact only and is never touched in production.
- **Report files**: stored in **Firebase Storage** under `reports/{id}.pdf` —
  covered by the bucket's lifecycle/backup policy.
- **Media/evidence**: back up `media/` (captured frames) — PDF reports embed
  copies, but keep the originals.
- **Migrations**: in production there is no local schema to migrate — the
  Firestore document structure is additive and the application never resets
  or restructures existing data.
- **Recovery drill**: verify Firestore export + one authorized report download
  (and `GET /health/ready`, which probes Firestore) on a staging box before
  promoting.
- The application **never resets the production database at startup**, and
  tests always run against mocked Firebase / an isolated throwaway SQLite
  file (`database/test_thermoguard.db`), never real data.

## 8. Secrets hygiene

- `.gitignore` already excludes `.env`, `database/*.db`, `logs/*.log`,
  `reports/generated/`, `media/`, models and node artifacts.
- Never commit `.env`, JWTs, API keys, database passwords, private keys or
  sensor credentials. Search the repo before release
  (`git grep -iE "secret_key|password|BEGIN (RSA|OPENSSH|EC) PRIVATE"`).
- Report API responses never include server filesystem paths (only safe
  metadata like `file_name`); the download endpoint resolves paths server-side.

## 9. Recommended deployment architecture

```
HTTPS (TLS terminator: Caddy / nginx / Traefik / Cloudflare)
   │
   ├── Dashboard  (Vite build → static hosting / Streamlit :8501)
   └── API        FastAPI :8000  (/api/v1, /health*, /mobile, /media, /reports)
                     │                ▲ WebSocket /api/v1/ws/inspect (upgrade)
                     ▼                │
               Firebase  ◄───────────┘  Authentication + Firestore + Storage
```

- Frontend hosting: static HTTPS host (Netlify/Vercel/Cloudflare Pages/S3+CDN)
  or a container with the built `dist/`.
- Backend hosting: any environment supporting Python 3.12 + WebSockets
  (Docker per `docker/Dockerfile`, k8s, VM). `docker/docker-compose.yml`
  provides the Firebase-backed reference compose file.
- Domain configuration: single apex domain with routes to dashboard + API so
  HTTPS and `wss://` share an origin; set `CORS_ORIGINS` and `ALLOWED_HOSTS`
  to that domain.

## 10. Security audit summary (what is enforced server-side)

- **Organization isolation**: devices, inspections, thermal history,
  anomalies, maintenance, reports, alarms, panels/components and assets are
  org-scoped; cross-org access returns 404 (no existence leak); NULL matches
  NULL; legacy (unowned) records stay visible to all authenticated users per
  the established policy.
- **IDOR/direct-ID**: report download, maintenance, inspection mutations,
  assets, alarms, analytics(predictive) all gate ownership before acting.
- **WebSocket**: auth before accept, one inspection per connection, message
  validation (unknown/malformed rejected), device/panel org validated on
  start, idempotent stop, abort only while running, no exception text leaked.
- **Report files**: no filesystem paths in API responses; downloads remain
  authorized with `Cache-Control: no-store`.
- **Cameras**: all camera control endpoints now require authentication.
- **Errors**: structured `{detail, code, details}` responses; no stack traces
  reach clients.
