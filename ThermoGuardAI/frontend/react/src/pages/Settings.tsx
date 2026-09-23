import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setToken } from "../api/client";
import { firebaseConfigured, firebaseSignOut } from "../firebase";
import { Badge, Button, Card, EmptyState } from "../components/ui";

interface HealthInfo {
  status?: string;
  app?: string;
  version?: string;
  database?: string;
  detector?: string;
  thermal?: string;
}

function readUser() {
  try {
    return JSON.parse(sessionStorage.getItem("tg_user") ?? "null");
  } catch {
    return null;
  }
}

export default function Settings() {
  const navigate = useNavigate();
  const [user] = useState(readUser);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [healthErr, setHealthErr] = useState("");

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealthErr("Backend health endpoint unreachable"));
  }, []);

  if (!user) {
    return (
      <Card>
        <EmptyState title="Not signed in" body="Sign in to view your profile." action={<Button variant="primary" onClick={() => navigate("/login")}>Sign in</Button>} />
      </Card>
    );
  }

  const signOut = () => {
    setToken(null);
    sessionStorage.removeItem("tg_user");
    // Also clear the Firebase Auth session when the app uses Firebase.
    if (firebaseConfigured) void firebaseSignOut();
    navigate("/login");
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="page-desc">Your profile, organization and system information.</p>
        </div>
      </div>

      <div className="grid-2">
        <Card title="Profile">
          <div className="setting-row">
            <div>
              <div className="sr-label">Name</div>
              <div className="sr-desc">{user.full_name || "—"}</div>
            </div>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Username</div>
              <div className="sr-desc">{user.username}</div>
            </div>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Email</div>
              <div className="sr-desc">{user.email || "—"}</div>
            </div>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Role</div>
              <div className="sr-desc">Controls what you can do in the platform</div>
            </div>
            <Badge tone={user.role === "admin" ? "tone-purple" : user.role === "technician" ? "tone-blue" : "tone-gray"}>{user.role}</Badge>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Organization</div>
              <div className="sr-desc">Your data is isolated to this organization</div>
            </div>
            <Badge tone="tone-cyan">{user.organization ?? "No organization"}</Badge>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Sign out</div>
              <div className="sr-desc">End this session</div>
            </div>
            <Button variant="danger" size="small" onClick={signOut}>Sign out</Button>
          </div>
        </Card>

        <Card title="Security & preferences">
          <div className="setting-row">
            <div>
              <div className="sr-label">Change password</div>
              <div className="sr-desc">Password changes are managed by an administrator on the backend.</div>
            </div>
            <Badge tone="plain">Not available</Badge>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Profile editing</div>
              <div className="sr-desc">Name/email changes are managed by an administrator.</div>
            </div>
            <Badge tone="plain">Not available</Badge>
          </div>
          <div className="setting-row">
            <div>
              <div className="sr-label">Notifications</div>
              <div className="sr-desc">The notifications panel shows real current events. Persistent push notifications are not yet supported by the backend.</div>
            </div>
            <Badge tone="plain">Status list</Badge>
          </div>
        </Card>
      </div>

      <Card title="System status" sub="Reported by the backend /health endpoint">
        {health ? (
          <div className="metric-grid">
            <div className="metric-card tone-green"><div className="m-label">Status</div><div className="m-value">{health.status ?? "—"}</div></div>
            <div className="metric-card tone-blue"><div className="m-label">Application</div><div className="m-value">{health.app ?? "—"}</div></div>
            <div className="metric-card tone-blue"><div className="m-label">Version</div><div className="m-value" style={{ fontSize: "1.1rem" }}>{health.version ?? "—"}</div></div>
            <div className="metric-card tone-cyan"><div className="m-label">Detector</div><div className="m-value" style={{ fontSize: "1.1rem" }}>{health.detector ?? "—"}</div></div>
            <div className="metric-card tone-amber"><div className="m-label">Thermal</div><div className="m-value" style={{ fontSize: "1.1rem" }}>{health.thermal ?? "—"}</div></div>
          </div>
        ) : (
          <p className="muted">{healthErr || "Loading system status…"}</p>
        )}
        <p className="disclaimer">
          {health?.thermal === "simulator" ? "The system is currently running with simulated thermal data (DEMO). Real thermal analytics will engage when a physical thermal sensor is connected." : ""}
        </p>
      </Card>
    </div>
  );
}
