# S6 — Firebase Production Integration Plan

**Status: IMPLEMENTED (Phase 2 complete).** The Firebase backend (`AUTH_BACKEND=firebase` /
`STORAGE_BACKEND=firestore`), Firebase Storage report handling, the FirestoreSession
data-layer shim, the React Firebase Authentication login, and offline (mocked) Firebase
tests are all implemented and verified. Production startup refuses to run unless Firebase
auth + Firestore storage are configured, and can never silently fall back to SQLite.

The S1–S5 application (SQLAlchemy/SQLite + bcrypt/custom-JWT) remains the verified
development/test baseline: 265 tests, Ruff clean, React build passing. SQLite is now
exclusively a development/test backend — production persistence is Cloud Firestore +
Firebase Storage.

## 0. Confirmed decisions (from the user)

- **Production data store**: **Cloud Firestore** (project `thermoguardai`).
  The web config also contains an RTDB URL, but Firestore is the confirmed
  application data store.
- **Auth providers**: Email/Password **+ Google + Phone (OTP)**.
- **Initial admin**: the backend **bootstraps** the admin user in Firebase
  Auth + Firestore on first production run (from `SEED_ADMIN_*` env vars).
- **Storage bucket**: `thermoguardai.firebasestorage.app`.

**Phone-OTP implication**: a user may sign in with a phone number only and
have no email. The Firestore `users/{uid}` profile therefore treats `email`
as nullable and keys identity by Firebase `uid` (never by email).
`username` is stored in the profile and unique-guarded via a
`usernames/{username}` lookup doc (Firestore has no unique index).

This plan maps the existing relational architecture onto Firebase **without
rewriting the service/API layer**. The production data store and identity
provider will be the Firebase project the user provides. The local SQLite +
bcrypt backend is retained **only** as the development/test backend, selected
by environment variables — production never depends on a local database.

---

## 1. Audit summary (what exists today)

| Layer | Implementation |
| --- | --- |
| Identity | bcrypt password hashes + custom HS256 JWT (`backend/core/security.py`), `sub` = SQL user id |
| Auth enforcement | `backend/api/deps.py` — `decode_and_get_user` (HTTP, WS via `?token=`, download via `?token=`) |
| Data access | SQLAlchemy 2.0 models → Repository pattern (`backend/repositories/base.py`) → services → API |
| DB | SQLite WAL; additive idempotent migrations in `backend/db/session.py`; seed in `backend/db/seed.py` |
| Org isolation | String `organization` column; NULL-matches-NULL; ownership **derived** via Device→Inspection→Report/Maintenance/Alarm and Panel→Component (`backend/services/isolation.py`) |
| Reports | PDF files on disk; metadata in `reports` table; authorized download endpoint; `file_available: bool` (never paths) |
| Thermal | Backend-authoritative `GET /api/v1/thermal/status`; MEASURED/SIMULATED/UNAVAILABLE/INVALID; NaN/Inf rejected |
| WebSocket | `/ws/inspect` — auth-before-accept, org/device checks, idempotent stop, no internals leaked |

**No Firebase code existed in the repository when this plan was written.**
Phase 2 implemented all of the above (see the sections below).

## 2. Entity mapping (SQL → Firebase)

| Existing SQL entity | Firebase equivalent | Notes |
| --- | --- | --- |
| `users` (username, email, password, role, organization, is_active) | Firebase Auth user (uid) **+** Firestore `users/{uid}` profile doc | Auth owns identity/credentials; Firestore owns role, organization, is_active (server-side only) |
| `devices` | Firestore `devices/{deviceId}` | Numeric id preserved via counter; `organization` field |
| `panels` | Firestore `panels/{panelId}` | `organization` optional (legacy NULL semantics preserved) |
| `components` | Firestore `components/{componentId}` | `panel_id` reference |
| `inspections` | Firestore `inspections/{inspectionId}` | `device_id`, `panel_id`, `user_id` refs; `thermal_source/simulated` preserved |
| `detections` | Firestore subcollection `inspections/{id}/detections/{detId}` | child of inspection |
| `faults` | Firestore subcollection `inspections/{id}/faults/{faultId}` | child of inspection |
| `temperature_history` | Firestore subcollection `inspections/{id}/temperature_readings/{rid}` | high-volume, child of inspection |
| `thermal_history` | Firestore `thermal_history/{historyId}` | top-level (analytics query across devices) |
| `reports` | Firestore `reports/{reportId}` (metadata) **+** Firebase Storage `reports/{reportId}.pdf` | path never exposed; `file_available` boolean |
| `maintenance` | Firestore `maintenance/{recordId}` | `inspection_id`, `incident_id` refs |
| `incidents` | Firestore `incidents/{incidentId}` | |
| `alarms` | Firestore `alarms/{alarmId}` | |
| `events` (log) | Firestore `events/{eventId}` | audit/activity log |
| `organizations` (implicit string) | Firestore `organizations/{orgKey}` | optional org metadata collection |

## 3. Service selection (what Firebase product does what)

| Requirement | Firebase service | Why |
| --- | --- | --- |
| Login/signup/logout, token verification | **Firebase Authentication** (email/password; Admin SDK verify) | Identity proof; `verify_id_token` server-side; email uniqueness built in |
| Application data (devices, inspections, history, reports metadata, maintenance) | **Firestore** | Document store for the domain model; org-aware queries; subcollections preserve one-to-many |
| Report PDF / evidence file storage | **Firebase Storage** | Blob storage with the existing authorized-download contract preserved (no public URLs) |
| Runtime/deploy config | **Firebase project config** via env vars | No App Check / Functions needed unless deployment moves to Cloud Run later |

**Not used:** Realtime Database, Functions, Hosting, Analytics — not required by the existing architecture.

## 4. Authentication architecture

- **Frontend**: Firebase JS SDK — `signInWithEmailAndPassword`, Google popup,
  or phone OTP (`signInWithPhoneNumber`) → ID token → sent as
  `Authorization: Bearer` (and `?token=` for WS/downloads).
- **Backend**: `firebase_admin.auth.verify_id_token(token, check_revoked=True)`
  → `uid` → load Firestore `users/{uid}` → role/organization/is_active
  **from the Firestore profile, never from client claims**.
- **Identity key = Firebase `uid`**. `email` is nullable (phone-only users);
  `username` is unique-guarded via `usernames/{username}` lookup docs.
- The existing `decode_and_get_user` / `get_current_user` /
  `get_current_user_ws` / `require_roles` dependency chain is preserved — only
  the token-decode step is swapped (Firebase verification instead of local
  JWT) behind the `AUTH_BACKEND` switch.
- Role + organization remain server-side state in Firestore. The backend is
  the security boundary; the frontend never decides organization, role, or
  ownership.
- Admin/technician/viewer RBAC and org isolation semantics (404 for cross-org,
  NULL-matches-NULL legacy policy) are enforced identically in the Firestore
  repository layer.

## 5. Firestore data model

Conceptual layout (final details confirmed during implementation):

```
users/{uid}                          # profile: username, email, role, organization, is_active
devices/{deviceId}                   # + organization
panels/{panelId}                     # + organization (nullable, legacy semantics)
components/{componentId}             # panel_id ref
inspections/{inspectionId}           # device_id, panel_id, user_id refs; thermal flags
inspections/{id}/detections/{detId}
inspections/{id}/faults/{faultId}
inspections/{id}/temperature_readings/{rid}
thermal_history/{historyId}          # device_id, inspection_id, organization
reports/{reportId}                   # metadata; Storage for the PDF
maintenance/{recordId}
incidents/{incidentId}
alarms/{alarmId}
events/{eventId}
```

- **IDs**: numeric IDs are preserved with atomic counter documents (`counters/{entityName}` using `increment`) so the existing API/frontend numeric-ID contract and the `TG-…`/`INC-…`/`MT-…` codes are unchanged.
- **Ownership**: the single `organization` field is written on device-linked docs **at creation time from the device's organization** (server-side, never client-supplied). Reads filter by `organization == caller.org` (with the legacy NULL-matches-NULL case) — the backend enforces isolation on every query; Firestore rules are locked down as defense-in-depth.
- **Timestamps**: Firestore timestamps (UTC) map to the existing `DateTime(timezone=True)` fields.
- **Duplication**: only `organization` is duplicated (with the write-time derivation rule above) — no other ownership fields are introduced.

## 6. Storage architecture

- Report PDFs go to Firebase Storage under `reports/` (Phase 2.1: generated in a **temporary directory**, uploaded, and the temp copy removed — production never depends on a local persistent reports directory).
- `file_available: bool` stays in the API. The download endpoint authorizes **then** streams the blob (`blob.download_as_bytes()`) — no public URLs, no signed URLs exposed, no filesystem paths, cross-org → 404.
- Firestore `reports/{reportId}` stores the storage path reference server-side only.
- **Media/evidence frames** currently write to a local `media/frames` directory (temporary working copies for PDF embedding). A separate migration to Firebase Storage (`inspections/{id}/…`) is recommended but was **not** auto-implemented — it requires approval (see `docs/PRODUCTION.md` §4).

## 6b. Security rules (Phase 2.1)

- `firestore.rules` and `storage.rules` ship in the repo root. Both **deny all direct client access** (`allow read, write: if false;`) because the backend (Firebase Admin SDK, which bypasses rules) is the only writer and the security boundary. Deploy with:
  ```bash
  firebase deploy --only firestore:rules,storage
  ```
- The React frontend never touches Firestore/Storage directly — it only calls the backend API.

## 7. Environment variables required (additions to `.env.example`)

| Variable | Purpose | Committed? |
| --- | --- | --- |
| `AUTH_BACKEND=local\|firebase` | backend selection | yes (no secret) |
| `STORAGE_BACKEND=sqlite\|firestore` | data-layer selection | yes |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to service-account JSON | no — secret (path only, file gitignored) |
| `FIREBASE_PROJECT_ID` | backend project id | yes (non-secret) |
| `FIREBASE_STORAGE_BUCKET` | bucket name | yes (non-secret) |
| `VITE_FIREBASE_*` | frontend web-app config (public) | yes (non-secret) |
| `FIRESTORE_EMULATOR_HOST` / `FIREBASE_AUTH_EMULATOR_HOST` / `FIREBASE_STORAGE_EMULATOR_HOST` | emulator testing | yes (dev/tests only) |

**Never committed:** `.env`, the service-account JSON file/contents, private keys, API keys, passwords.

## 8. Testing strategy

- All **235 existing tests keep passing** against the local SQLite + bcrypt backend (isolated `test_thermoguard.db`).
- New Firebase integration tests run against the **Firebase Emulator Suite** (Firestore/Auth/Storage emulators) — never the real project.
- New tests: Firebase token verification (valid/expired/malformed/revoked), Firestore persistence, org isolation, report-storage authorization, WS Firebase auth.

## 9. What stays untouched

- The real local DB (42 inspections / 9 reports / 0 devices) — **no migration runs automatically**. A data-migration plan (copy to Firestore) requires explicit approval before execution.
- The thermal subsystem, analytics thresholds, security model, UI, and all S1–S5 behavior.

---

## 10. EXACT credentials / configuration required from you (STOP GATE)

**Received so far:**

- [x] Firebase project ID — `thermoguardai`
- [x] Frontend Web App config (`apiKey`, `authDomain`, `storageBucket`
      `thermoguardai.firebasestorage.app`, `appId`, etc.) — public, frontend-only
- [x] Data store decision — Cloud Firestore
- [x] Auth providers — Email/Password + Google + Phone OTP
- [x] Admin plan — backend bootstraps the admin from `SEED_ADMIN_*`

**Still required (backend Admin SDK cannot initialize without it):**

- [ ] **Backend service-account JSON** — generate at Firebase Console →
      Project settings → Service accounts → *Generate new private key* →
      download the JSON (contains `project_id`, `client_email`, `private_key`).
      Provide the file contents (or a server path where the backend can read
      it). It is referenced **only** via `GOOGLE_APPLICATION_CREDENTIALS` /
      env vars and is never committed to the repository.

### Manual steps you must perform in Firebase Console (for reference)

1. Confirm the existing Firebase project `thermoguardai`.
2. **Authentication** → Sign-in method → enable **Email/Password**, **Google**
   and **Phone**. Add the domain(s) the app runs on to Authorized domains.
3. **Cloud Firestore** → Create database (production mode; any region — the
   RTDB URL indicates europe-west1 is available). **As of the Phase 2.1 audit
   the Firestore API is DISABLED in project `thermoguardai` (403) — this step
   is a hard blocker.**
4. **Storage** → create the bucket `thermoguardai.firebasestorage.app`
   (currently 404/does not exist — hard blocker); deploy `storage.rules`.
5. Generate the **service-account private key** (step above).
6. Register the **Web app** (already exists) — the config you pasted is used
   in the React build via `VITE_FIREBASE_*` env vars.
7. Deploy `firestore.rules` + `storage.rules` (see §6b).
8. The backend bootstraps the admin user on first production run — no manual
   admin creation needed unless you prefer it.

---

**Next step: provide the credentials above. I will then implement the
`AUTH_BACKEND=firebase` / `STORAGE_BACKEND=firestore` backends behind the
existing repository/dependency layer, add emulator-based tests, and verify the
full login + persistence + storage + isolation flow against YOUR project —
without touching the local DB or any existing tests.**
