import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Device, type DeviceMaintenanceResult, type MaintenanceListItem } from "../api/client";
import { Badge, Card, EmptyState, ErrorState, LoadingState, MetricCard, RiskBadge, StatusBadge, fmtDate } from "../components/ui";

const ATTENTION_STATUSES = new Set(["ATTENTION", "HIGH_RISK", "CRITICAL", "MAINTENANCE"]);

export default function Maintenance() {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<MaintenanceListItem[]>([]);
  const [byStatus, setByStatus] = useState<Record<string, number>>({});
  const [devices, setDevices] = useState<Device[]>([]);
  const [recs, setRecs] = useState<Record<number, DeviceMaintenanceResult>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.listMaintenance(), api.listDevices()])
      .then(async ([m, d]) => {
        setOrders(m.items);
        setByStatus(m.by_status);
        setDevices(d);
        // Real per-device recommendations for devices that need attention
        // (bounded — never thousands of requests).
        const attention = d.filter((x) => ATTENTION_STATUSES.has(x.derived_status)).slice(0, 20);
        const entries = await Promise.all(
          attention.map(async (dev) => {
            try {
              const r = await api.getDeviceMaintenance(dev.id);
              return [dev.id, r] as const;
            } catch {
              return [dev.id, null] as const;
            }
          })
        );
        const recMap: Record<number, DeviceMaintenanceResult> = {};
        entries.forEach(([id, r]) => {
          if (r) recMap[id] = r;
        });
        setRecs(recMap);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingState label="Loading maintenance data…" />;
  if (error) return <ErrorState message={error} />;

  const today = new Date().toISOString().slice(0, 10);
  const attention = devices.filter((d) => ATTENTION_STATUSES.has(d.derived_status));
  const overdue = devices.filter((d) => d.next_inspection_date && d.next_inspection_date < today);
  const upcoming = devices.filter((d) => {
    const n = d.next_inspection_date;
    if (!n) return false;
    const days = (new Date(n).getTime() - new Date(today).getTime()) / 86400000;
    return days >= 0 && days <= 30;
  });

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Maintenance Center</h1>
          <p className="page-desc">Recommendations generated from actual inspection evidence and device metadata.</p>
        </div>
      </div>

      <div className="metric-grid">
        <MetricCard label="Pending orders" value={byStatus.pending ?? 0} tone="amber" icon="◷" />
        <MetricCard label="In progress" value={byStatus.in_progress ?? 0} tone="blue" icon="⚙" />
        <MetricCard label="Completed" value={byStatus.completed ?? 0} tone="green" icon="✓" />
        <MetricCard label="Devices requiring maintenance" value={attention.length} tone="red" icon="🔧" />
        <MetricCard label="Overdue inspections" value={overdue.length} tone="orange" icon="⚠" />
        <MetricCard label="Upcoming (30 days)" value={upcoming.length} tone="cyan" icon="◷" />
      </div>

      <div className="grid-2">
        <Card title="Devices requiring attention" sub="Real derived status + recommendation from the S2 engine">
          {attention.length === 0 ? (
            <EmptyState title="All clear" body="No devices currently require attention." />
          ) : (
            <div className="row-list">
              {attention.map((d) => {
                const rec = recs[d.id];
                return (
                  <div key={d.id} className="row-item" style={{ cursor: "pointer" }} onClick={() => navigate(`/devices/${d.id}`)}>
                    <div className="ri-main">
                      <div className="ri-title">{d.name}</div>
                      <div className="ri-sub">Status: {d.derived_status} · last inspection {fmtDate(d.last_inspection_at, false)}</div>
                      {rec?.recommendation && (
                        <div className="ri-sub" style={{ color: "var(--amber)", marginTop: 4 }}>→ {rec.recommendation}</div>
                      )}
                    </div>
                    <StatusBadge status={d.derived_status} />
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Inspection schedule" sub="From next_inspection_date device metadata">
          <h3 style={{ fontSize: "0.82rem", marginBottom: 8 }}>Overdue</h3>
          {overdue.length === 0 ? (
            <p className="muted" style={{ fontSize: 13 }}>No overdue inspections.</p>
          ) : (
            <div className="row-list">
              {overdue.map((d) => (
                <div key={d.id} className="row-item">
                  <span className="ri-main"><span className="ri-title">{d.name}</span><span className="ri-sub">due {d.next_inspection_date}</span></span>
                  <Badge tone="tone-red">overdue</Badge>
                </div>
              ))}
            </div>
          )}
          <h3 style={{ fontSize: "0.82rem", margin: "14px 0 8px" }}>Upcoming (30 days)</h3>
          {upcoming.length === 0 ? (
            <p className="muted" style={{ fontSize: 13 }}>No inspections scheduled in the next 30 days.</p>
          ) : (
            <div className="row-list">
              {upcoming.map((d) => (
                <div key={d.id} className="row-item">
                  <span className="ri-main"><span className="ri-title">{d.name}</span><span className="ri-sub">due {d.next_inspection_date}</span></span>
                  <Badge tone="tone-cyan">{d.next_inspection_date}</Badge>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="Recommendations from the S2 engine" sub="Per-device maintenance recommendations with their evidence basis">
        {Object.keys(recs).length === 0 ? (
          <EmptyState title="No recommendations" body="Per-device recommendations appear for devices with attention-level status." />
        ) : (
          <div className="row-list">
            {devices
              .filter((d) => recs[d.id])
              .map((d) => (
                <div key={d.id} className="row-item" style={{ cursor: "pointer" }} onClick={() => navigate(`/devices/${d.id}`)}>
                  <span className="ri-main">
                    <span className="ri-title">{d.name}</span>
                    <span className="ri-sub">{recs[d.id].recommendation}</span>
                  </span>
                  <RiskBadge level={recs[d.id].risk_level} />
                </div>
              ))}
          </div>
        )}
      </Card>

      <Card title="Work orders" sub="Maintenance records for your organization">
        {orders.length === 0 ? (
          <EmptyState title="No maintenance orders" body="Maintenance orders are created automatically for critical findings or manually by technicians." />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Fault type</th>
                  <th>Priority</th>
                  <th>Status</th>
                  <th>Assigned</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {orders.slice(0, 50).map((o) => (
                  <tr key={o.id}>
                    <td className="mono">{o.code}</td>
                    <td>{o.fault_type ?? "—"}</td>
                    <td><Badge tone={o.priority === "critical" ? "tone-red" : o.priority === "high" ? "tone-orange" : o.priority === "medium" ? "tone-amber" : "tone-gray"}>{o.priority}</Badge></td>
                    <td><Badge tone={o.status === "completed" ? "tone-green" : o.status === "in_progress" ? "tone-blue" : "tone-amber"}>{o.status}</Badge></td>
                    <td>{o.assigned_to ?? "—"}</td>
                    <td>{fmtDate(o.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
