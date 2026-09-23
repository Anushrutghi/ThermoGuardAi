import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type DashboardData, type Device } from "../api/client";
import { BarsChart, RiskDonut } from "../components/charts";
import { Card, EmptyState, ErrorState, LoadingState, MetricCard, RiskBadge, StatusBadge, fmtDate, fmtTemp } from "../components/ui";

type Stats = { total: number; today: number; open_alarms: number; avg_risk: number; by_severity: Record<string, number> };

export default function Dashboard() {
  const navigate = useNavigate();
  const [dash, setDash] = useState<DashboardData | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [weekCount, setWeekCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.dashboard(), api.listDevices(), api.stats(), api.inspectionsPerDay(7)])
      .then(([d, devs, s, wk]) => {
        setDash(d);
        setDevices(devs);
        setStats(s);
        setWeekCount(wk.items.reduce((acc, x) => acc + x.count, 0));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingState label="Loading operational dashboard…" />;
  if (error) return <ErrorState message={error} onRetry={() => window.location.reload()} />;
  if (!dash) return <EmptyState title="No dashboard data" body="The dashboard will populate once devices are registered." />;

  const riskBars = [
    { name: "NORMAL", label: "Healthy", value: dash.healthy, color: "var(--green)" },
    { name: "ELEVATED", label: "Attention", value: dash.attention, color: "var(--amber)" },
    { name: "ABNORMAL", label: "High risk", value: dash.high_risk, color: "var(--orange)" },
    { name: "CRITICAL", label: "Critical", value: dash.critical, color: "var(--red)" },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Operational Dashboard</h1>
          <p className="page-desc">Live status of monitored electrical devices, built from real inspection data.</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" onClick={() => navigate("/live")}>
            ▶ Start inspection
          </button>
        </div>
      </div>

      <div className="metric-grid">
        <MetricCard label="Total devices" value={dash.total_devices} tone="blue" icon="◧" sub={dash.total_devices === 0 ? "No devices registered yet" : `${dash.monitoring} under monitoring`} />
        <MetricCard label="Healthy" value={dash.healthy} tone="green" icon="✓" />
        <MetricCard label="Attention" value={dash.attention} tone="amber" icon="⚠" />
        <MetricCard label="High risk" value={dash.high_risk} tone="orange" icon="▲" />
        <MetricCard label="Critical" value={dash.critical} tone="red" icon="✕" />
        <MetricCard label="Inspections this month" value={dash.inspections_this_month} tone="cyan" icon="◷" />
        <MetricCard label="Anomalies this month" value={dash.anomalies_this_month} tone="red" icon="◆" />
        <MetricCard label="Maintenance required" value={dash.devices_requiring_maintenance} tone="purple" icon="🔧" />
      </div>

      {dash.total_devices === 0 && (
        <Card>
          <EmptyState
            icon="⚡"
            title="No devices registered yet"
            body="Create a device from the Devices page, then run a Live Inspection to start building real history. Every number here comes from actual inspection data."
            action={<button className="btn primary" onClick={() => navigate("/devices")}>Go to Devices</button>}
          />
        </Card>
      )}

      {stats && (
        <div className="metric-grid">
          <MetricCard label="Inspections today" value={stats.today} tone="green" sub="Completed sessions today" />
          <MetricCard label="Inspections this week" value={weekCount ?? "—"} tone="cyan" sub="Last 7 days · real records" />
          <MetricCard label="Open alarms" value={stats.open_alarms} tone="red" sub="Unacknowledged alarms" />
          <MetricCard label="Avg risk · 30 days" value={`${stats.avg_risk}%`} tone="amber" sub="Mean inspection risk score" />
        </div>
      )}
      {stats && dash.total_devices > 0 && (
        <div className="metric-grid">
          <MetricCard
            label="Temperature range"
            value={dash.avg_temperature != null ? `${dash.avg_temperature}°` : "—"}
            tone="blue"
            sub={dash.max_temperature != null ? `Max ${dash.max_temperature}° · real sensors only` : "No real thermal data"}
          />
        </div>
      )}

      <div className="grid-2">
        <Card title="Risk distribution" sub="Devices by derived operational status">
          {dash.total_devices > 0 ? (
            <RiskDonut
              data={riskBars.filter((r) => r.value > 0).map((r) => ({ name: r.label, value: r.value, color: r.color }))}
              onSlice={() => navigate("/devices")}
            />
          ) : (
            <EmptyState title="No risk distribution" body="Risk levels appear once devices have inspection history." />
          )}
        </Card>
        <Card title="Fault severity · 30 days" sub="From inspection fault classification">
          {stats && Object.keys(stats.by_severity).length > 0 ? (
            <BarsChart
              data={Object.entries(stats.by_severity).map(([k, v]) => ({ label: k, count: v }))}
              dataKey="count"
              color={stats.by_severity.critical ? "var(--red)" : "var(--amber)"}
            />
          ) : (
            <EmptyState title="No fault data" body="No inspections with classified faults in the last 30 days." />
          )}
        </Card>
      </div>

      <Card title="Device health overview" sub="Latest real readings — health reflects risk, not a certified measurement">
        {devices.length === 0 ? (
          <EmptyState title="No devices" body="No devices registered in your organization yet." />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Location</th>
                  <th>Status</th>
                  <th>Risk</th>
                  <th>Latest temp</th>
                  <th>Last inspection</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id} className="rowlink" onClick={() => navigate(`/devices/${d.id}`)}>
                    <td style={{ fontWeight: 600, color: "var(--text)" }}>{d.name}</td>
                    <td>{d.location ?? "—"}</td>
                    <td><StatusBadge status={d.derived_status} /></td>
                    <td><RiskBadge level={d.risk_level} /></td>
                    <td className="num">{d.last_max_temp != null ? fmtTemp(d.last_max_temp, 1) : "—"}{d.last_thermal_simulated ? " (DEMO)" : ""}</td>
                    <td>{fmtDate(d.last_inspection_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="disclaimer mt-1">Device health/risk scores are heuristic indicators derived from inspection data — not a scientifically validated engineering measurement.</p>
      </Card>
    </div>
  );
}
