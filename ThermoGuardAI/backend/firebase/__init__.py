"""Firebase integration for ThermoGuard AI (S6).

Provides the production identity + persistence backend:

- ``client``  — lazy, emulator-aware Firebase Admin SDK initialization
- ``auth``    — server-side Firebase ID-token verification
- ``users``   — Firestore user profiles, organization registry, admin bootstrap
- ``engine``  — FirestoreSession shim + query engine backing the existing
               repository/service layer when STORAGE_BACKEND=firestore
- ``storage`` — Firebase Storage-backed report files (no public URLs)

The local SQLite/bcrypt backend remains the development/test backend; this
package is only exercised when AUTH_BACKEND=firebase / STORAGE_BACKEND=firestore.
Credentials are never committed and never logged — they are referenced by path
via FIREBASE_SERVICE_ACCOUNT_PATH / GOOGLE_APPLICATION_CREDENTIALS.
"""
from __future__ import annotations
