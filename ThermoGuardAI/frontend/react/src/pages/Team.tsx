import { useCallback, useEffect, useState } from "react";
import { api, type TeamUser } from "../api/client";
import { Badge, Card, EmptyState, LoadingState, fmtDate } from "../components/ui";

export default function Team() {
  const [users, setUsers] = useState<TeamUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .listUsers()
      .then((r) => setUsers(r.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Team</h1>
          <p className="page-desc">Members of your organization. Organization isolation is enforced by the backend — you only see your own organization's users.</p>
        </div>
      </div>

      {error && (
        <div className="warn-box">
          {error}. Team management requires an administrator account.
        </div>
      )}
      {loading && !error ? (
        <LoadingState label="Loading team…" />
      ) : (
        <Card tight>
          {!error && users.length === 0 ? (
            <EmptyState title="No team members" body="Users registered to your organization will appear here." />
          ) : !error ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Joined</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => (
                    <tr key={u.id}>
                      <td style={{ fontWeight: 600, color: "var(--text)" }}>{u.full_name || u.username}</td>
                      <td>{u.email}</td>
                      <td>
                        <Badge tone={u.role === "admin" ? "tone-purple" : u.role === "technician" ? "tone-blue" : "tone-gray"}>{u.role}</Badge>
                      </td>
                      <td>
                        <Badge tone={u.is_active ? "tone-green" : "tone-red"}>{u.is_active ? "Active" : "Disabled"}</Badge>
                      </td>
                      <td>{fmtDate(u.created_at, false)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Card>
      )}
      <p className="disclaimer">User provisioning (invites, role changes, disabling) is managed by the backend and is not exposed in this UI build.</p>
    </div>
  );
}
