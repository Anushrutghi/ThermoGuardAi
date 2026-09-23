// Firebase Web SDK — public client configuration (S6).
//
// ONLY the public Firebase Web App config is used here (apiKey, authDomain,
// projectId, storageBucket, messagingSenderId, appId). These are non-secret
// values designed to be embedded in a client app. Admin SDK service-account
// credentials NEVER appear in the frontend — they stay backend-only
// (GOOGLE_APPLICATION_CREDENTIALS).
//
// Production builds MUST use Firebase Authentication. The legacy
// username/password flow is only reachable when the build is explicitly
// configured with VITE_AUTH_MODE=local (development/tests against the local
// backend) — it can never be selected accidentally in production.

import { initializeApp } from "firebase/app";
import type { FirebaseApp } from "firebase/app";
import {
  GoogleAuthProvider,
  RecaptchaVerifier,
  getAuth,
  signInWithEmailAndPassword,
  signInWithPhoneNumber,
  signInWithPopup,
} from "firebase/auth";
import type { Auth, ConfirmationResult, UserCredential } from "firebase/auth";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

/** True when the build was configured with a real Firebase Web App config. */
export const firebaseConfigured: boolean = Boolean(
  firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.authDomain
);

/**
 * True only when the build EXPLICITLY opts into the legacy local
 * username/password flow (VITE_AUTH_MODE=local). Production builds never set
 * this, so the legacy flow is unreachable in production.
 */
export const legacyAuthEnabled: boolean = import.meta.env.VITE_AUTH_MODE === "local";

let app: FirebaseApp | null = null;
let auth: Auth | null = null;

function getApp(): FirebaseApp {
  if (!app) app = initializeApp(firebaseConfig);
  return app;
}

export function getFirebaseAuth(): Auth {
  if (!auth) auth = getAuth(getApp());
  return auth;
}

/** Sign in with email/password and return the Firebase ID token. */
export async function firebaseEmailPasswordLogin(email: string, password: string): Promise<string> {
  const cred: UserCredential = await signInWithEmailAndPassword(getFirebaseAuth(), email, password);
  return cred.user.getIdToken();
}

/** Sign in with the Google popup provider and return the Firebase ID token. */
export async function firebaseGoogleLogin(): Promise<string> {
  const cred: UserCredential = await signInWithPopup(getFirebaseAuth(), new GoogleAuthProvider());
  return cred.user.getIdToken();
}

/**
 * Start a phone-OTP sign-in: sends the SMS code and returns a
 * ConfirmationResult to verify it with `firebaseConfirmPhoneCode`.
 *
 * `containerId` must reference a hidden div used by the invisible reCAPTCHA.
 * In the Firebase Auth emulator no reCAPTCHA is needed; in production the
 * Phone provider must be enabled and the domain authorized.
 */
export async function firebaseSendPhoneCode(phoneNumber: string, containerId: string): Promise<ConfirmationResult> {
  const authInstance = getFirebaseAuth();
  const verifier = new RecaptchaVerifier(authInstance, containerId, { size: "invisible" });
  return signInWithPhoneNumber(authInstance, phoneNumber, verifier);
}

/** Verify a phone-OTP code and return the Firebase ID token. */
export async function firebaseConfirmPhoneCode(confirmation: ConfirmationResult, code: string): Promise<string> {
  const cred: UserCredential = await confirmation.confirm(code);
  return cred.user.getIdToken();
}

/** Sign out of Firebase (best-effort) — clears the Firebase session. */
export async function firebaseSignOut(): Promise<void> {
  if (firebaseConfigured && auth) {
    try {
      await auth.signOut();
    } catch {
      // Best-effort: local session cleanup always proceeds.
    }
  }
}
