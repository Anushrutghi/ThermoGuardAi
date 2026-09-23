# Security Model (S5)

ThermoGuard AI's security model. **Every authorization decision is made
server-side.** Cross-organization access always fails closed with **404** (not
403) so resource existence is never revealed.

## 1. Authentication

- JWT (HS256) access tokens with `iat`/`exp` validation. Malformed tokens
  (missing/foreign `sub`, wrong types) are rejected exactly like expired ones.
- Passwords are hashed with bcrypt; plaintext passwords never leave the login
  request and are never logged.
- WebSocket connections authenticate **before** `accept()` via `?token=`
  (close code 4401 on failure).
- Report downloads accept the `Authorization` header or `?token=` query
  parameter; both paths resolve the user server-side before any file is
  returned.

## 2. Organization isolation

Ownership chains (all strict, NULL matches NULL):

```
Organization → Device → Inspection → { Report, Maintenance, Alarm,
                                       ThermalHistory, Anomaly }
Organization → Panel → Component
```

- Every device-linked record is only visible to the owning organization.
- **Unowned legacy records** (no device / device-less inspection / NULL-org
  panel) remain visible to all authenticated users — the 42 legacy
  inspections are preserved and treated as platform-level.
- Cross-org lookups, mutations and downloads return **404**. Lists are
  org-scoped; foreign rows never appear.
- Panels carry optional `organization`; org-scoped panels are only usable by
  their own organization. Maintenance creation validates the resolved
  component's panel ownership.

## 3. Authorization by role

- `admin`: full device/panel/inspection/report lifecycle, manual status
  override, team management.
- `technician`: inspections, reports, maintenance; manual MAINTENANCE status
  is denied (403).
- `viewer`: read-only.

Every protected resource **fails closed** by default.

## 4. WebSocket security

- Authentication before start; organization + device ownership validated when
  an inspection is created.
- One inspection per connection; duplicate `start_inspection` is rejected.
- Malformed JSON and unknown message types get explicit error responses.
- Frames are size-capped (8 MB). Duplicate stop is idempotent (always acked).
- A client disconnect aborts only a **running** inspection — a gracefully
  completed inspection is never re-marked aborted.
- No stack traces or internals are ever sent to clients.

## 5. Report / file security

- `ReportOut` exposes **no filesystem paths** — only safe metadata plus
  `file_available: true/false`.
- Downloads are authorized per-request (Bearer or `?token=`) and org-scoped;
  missing files return a safe 404. Responses carry `Cache-Control: no-store`.
- Static media is mounted read-only under `/media` and `/reports`.

## 6. CORS & hosts

- Production rejects `CORS_ORIGINS=*` with credentials; origins must be
  explicit.
- `TrustedHostMiddleware` is enabled in production with explicit
  `ALLOWED_HOSTS` (mitigates Host-header / DNS-rebinding).

## 7. Input validation

- Frame validation rejects NaN/±Inf, out-of-range temperatures, non-numeric
  dtypes and invalid shapes before any analytics (see
  `docs/THERMAL_HARDWARE.md` §4).
- Pydantic schemas validate payloads; oversized WS payloads and invalid IDs
  fail safely.

## 8. Secrets

- `.env` / secrets / private keys are never committed (see `.gitignore` and
  `.env.example`). A repository secret scan is part of the QA checklist.
- Logging never includes passwords, tokens, authorization headers, API keys,
  database credentials or full camera frames.
