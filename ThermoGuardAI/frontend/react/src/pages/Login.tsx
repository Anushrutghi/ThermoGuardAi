import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createUserWithEmailAndPassword } from "firebase/auth";
import type { ConfirmationResult } from "firebase/auth";
import { api, setToken } from "../api/client";
import { Button, Field } from "../components/ui";
import {
  firebaseConfigured,
  firebaseConfirmPhoneCode,
  firebaseEmailPasswordLogin,
  firebaseGoogleLogin,
  firebaseSendPhoneCode,
  getFirebaseAuth,
  legacyAuthEnabled,
} from "../firebase";

/**
 * S6 — Firebase Authentication login.
 *
 * Production flow: the user signs in with Firebase Auth (email/password,
 * Google popup, or phone OTP), the ID token is exchanged at
 * POST /api/v1/auth/firebase for the application session token, and the app
 * stores that app JWT. Role and organization are granted server-side from the
 * Firestore profile — never from the client.
 *
 * The legacy username/password flow is only rendered when the build is
 * EXPLICITLY configured with VITE_AUTH_MODE=local (development/tests against
 * the local backend). If Firebase is not configured and legacy mode was not
 * opted in, a clear error is shown instead — production can never silently
 * fall back to local authentication.
 */
export default function Login() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [authTab, setAuthTab] = useState<"email" | "phone">("email");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneCode, setPhoneCode] = useState("");
  const [confirmation, setConfirmation] = useState<ConfirmationResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const completeLogin = (accessToken: string, user: { username: string; email?: string | null; role: string; organization?: string | null; id: number | string }) => {
    setToken(accessToken);
    sessionStorage.setItem("tg_user", JSON.stringify(user));
    navigate("/");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (firebaseConfigured) {
        // Firebase Auth: sign in (or create the account), then exchange the
        // ID token for the application session. New accounts are provisioned
        // server-side as viewer with no organization.
        if (mode === "signup") {
          await createUserWithEmailAndPassword(getFirebaseAuth(), email, password);
        }
        const idToken = await firebaseEmailPasswordLogin(email, password);
        const resp = await api.firebaseLogin(idToken);
        completeLogin(resp.access_token, resp.user);
      } else if (legacyAuthEnabled) {
        // Legacy local flow — ONLY when explicitly opted in via VITE_AUTH_MODE=local.
        if (mode === "signup") {
          await api.register(username, email, password, fullName);
        }
        const resp = await api.login(username, password);
        completeLogin(resp.access_token, resp.user);
      } else {
        setError("Firebase Authentication is not configured for this build (set VITE_FIREBASE_* and rebuild).");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  };

  const googleLogin = async () => {
    if (!firebaseConfigured) return;
    setBusy(true);
    setError("");
    try {
      const idToken = await firebaseGoogleLogin();
      const resp = await api.firebaseLogin(idToken);
      completeLogin(resp.access_token, resp.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Google sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  const sendPhoneCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!firebaseConfigured) return;
    setBusy(true);
    setError("");
    try {
      const result = await firebaseSendPhoneCode(phone, "recaptcha-container");
      setConfirmation(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send verification code");
    } finally {
      setBusy(false);
    }
  };

  const verifyPhoneCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!confirmation) return;
    setBusy(true);
    setError("");
    try {
      const idToken = await firebaseConfirmPhoneCode(confirmation, phoneCode);
      const resp = await api.firebaseLogin(idToken);
      completeLogin(resp.access_token, resp.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid verification code");
    } finally {
      setBusy(false);
    }
  };

  // Firebase unconfigured + legacy not opted in → clear configuration error.
  if (!firebaseConfigured && !legacyAuthEnabled) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="auth-brand">
            <span className="brand-mark">⚡</span>
            <h1>ThermoGuard AI</h1>
            <p>Electrical safety · Thermal inspection · Device monitoring · Predictive maintenance</p>
          </div>
          <div className="error-box">
            Firebase Authentication is not configured for this build. Set the public VITE_FIREBASE_* environment
            variables (and VITE_AUTH_MODE=local only for local development) and rebuild.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-mark">⚡</span>
          <h1>ThermoGuard AI</h1>
          <p>Electrical safety · Thermal inspection · Device monitoring · Predictive maintenance</p>
        </div>
        <div className="auth-tabs" role="tablist">
          <button role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "active" : ""} onClick={() => setMode("signin")}>
            Sign in
          </button>
          <button role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "active" : ""} onClick={() => setMode("signup")}>
            {firebaseConfigured ? "Create account" : "Sign up"}
          </button>
        </div>
        {firebaseConfigured && mode === "signin" && (
          <div className="auth-tabs" role="tablist" style={{ marginTop: 10 }}>
            <button role="tab" aria-selected={authTab === "email"} className={authTab === "email" ? "active" : ""} onClick={() => setAuthTab("email")}>
              Email
            </button>
            <button role="tab" aria-selected={authTab === "phone"} className={authTab === "phone" ? "active" : ""} onClick={() => setAuthTab("phone")}>
              Phone
            </button>
          </div>
        )}

        {authTab === "email" ? (
          <form onSubmit={submit}>
            {mode === "signup" && !firebaseConfigured && (
              <Field label="Full name">
                <input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Jane Doe" autoComplete="name" />
              </Field>
            )}
            {!firebaseConfigured && (
              <Field label="Username">
                <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required autoComplete="username" />
              </Field>
            )}
            <Field label="Email">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required={firebaseConfigured || mode === "signup"}
                placeholder={firebaseConfigured ? "you@example.com" : "jane@example.com"}
                autoComplete="email"
              />
            </Field>
            <Field label="Password">
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} autoComplete={mode === "signin" ? "current-password" : "new-password"} />
            </Field>
            {error && <div className="error-box">{error}</div>}
            <Button variant="primary" block busy={busy} type="submit">
              {mode === "signin" ? "Sign in" : "Create account & sign in"}
            </Button>
          </form>
        ) : (
          <form onSubmit={confirmation ? verifyPhoneCode : sendPhoneCode}>
            {!confirmation ? (
              <Field label="Phone number" hint="Includes country code, e.g. +1 555 010 1234">
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  required
                  placeholder="+1 555 010 1234"
                  autoComplete="tel"
                />
              </Field>
            ) : (
              <Field label="Verification code">
                <input type="text" inputMode="numeric" value={phoneCode} onChange={(e) => setPhoneCode(e.target.value)} required placeholder="6-digit code" autoComplete="one-time-code" />
              </Field>
            )}
            {error && <div className="error-box">{error}</div>}
            {confirmation ? (
              <Button variant="primary" block busy={busy} type="submit">
                Verify code
              </Button>
            ) : (
              <Button variant="primary" block busy={busy} type="submit">
                Send code
              </Button>
            )}
            {confirmation && (
              <button type="button" className="btn ghost block small" style={{ marginTop: 8 }} onClick={() => setConfirmation(null)} disabled={busy}>
                Change phone number
              </button>
            )}
            {/* Invisible reCAPTCHA host element (Firebase phone auth) */}
            <div id="recaptcha-container" />
          </form>
        )}

        {firebaseConfigured && authTab === "email" && (
          <div className="auth-divider">
            <span>or</span>
          </div>
        )}
        {firebaseConfigured && authTab === "email" && (
          <Button variant="default" block busy={busy} onClick={googleLogin}>
            <span aria-hidden="true">🌐</span>&nbsp; Continue with Google
          </Button>
        )}
        {mode === "signin" && (
          <div className="auth-foot">
            {firebaseConfigured
              ? "Authentication is provided by Firebase. Password resets are managed by your organization administrator."
              : "Legacy local authentication (VITE_AUTH_MODE=local). Password resets are managed by your organization administrator."}
          </div>
        )}
        {mode === "signup" && (
          <div className="auth-foot">
            {firebaseConfigured
              ? "New accounts are created as viewer with no organization, and must be activated and assigned by an administrator."
              : "New accounts are created as viewer with no organization, and must be activated and assigned by an administrator."}
          </div>
        )}
      </div>
    </div>
  );
}
