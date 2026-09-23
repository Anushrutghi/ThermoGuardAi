import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type AnomalyCenterItem, type Device } from "../api/client";
import { Badge, Card, EmptyState, ErrorState, LoadingState, fmtDate, fmtTemp } from "../components/ui";

const PAGE = 25;

export default function Anomalies() {
  const navigate = useNavigate();
  const [items, setItems] = useState<AnomalyCenterItem[]>([]);
  const [total, setTotal] = useState(0);
  const [devices, setDevices] = useState<Device[]>([]);
  const [severity, setSeverity] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.listDevices().then(setDevices).catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .listAnomalies({
        severity: severity || undefined,
        device_id: deviceId ? Number(deviceId) : undefined,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
        limit: PAGE,
        offset,
      })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [severity, deviceId, fromDate, toDate, offset]);
  useEffect(load, [load]);

  const repeated = items.filter((a) => a.repeated).length;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Anomaly Center</h1>
          <p className="page-desc">{total} detected anomalies · repeated anomalies flagged where the same kind recurred across inspections</p>
        </div>
      </div>

      {repeated > 0 && (
        <div className="warn-box mb-2">{repeated} item{repeated === 1 ? "" : "s"} on this page are repeated anomalies — the same anomaly kind was found in more than one inspection of that device.</div>
      )}

      <div className="toolbar">
        <select aria-label="Filter by severity" value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }} style={{ width: 150 }}>
          <option value="">All severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="warning">Warning</option>
        </select>
        <select aria-label="Filter by device" value={deviceId} onChange={(e) => { setDeviceId(e.target.value); setOffset(0); }} style={{ width: 220 }}>
          <option value="">All devices</option>
          {devices.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
        <input type="date" aria-label="From date" value={fromDate} onChange={(e) => { setFromDate(e.target.value); setOffset(0); }} style={{ width: 160 }} />
        <input type="date" aria-label="To date" value={toDate} onChange={(e) => { setToDate(e.target.value); setOffset(0); }} style={{ width: 160 }} />
      </div>

      {error && <ErrorState message={error} onRetry={load} />}
      {loading ? (
        <LoadingState label="Loading anomalies…" />
      ) : (
        <Card tight>
          {items.length === 0 ? (
            <EmptyState
              icon="✓"
              title={total === 0 ? "No anomalies detected" : "No anomalies match your filters"}
              body={total === 0 ? "No detected anomalies are recorded in the database yet." : "Try widening the date range or clearing filters."}
            />
          ) : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Severity</th>
                      <th>Device</th>
                      <th>Anomaly</th>
                      <th>Temp</th>
                      <th>Repeat</th>
                      <th>Date</th>
                      <th>Inspection</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((a) => (
                      <tr key={a.fault_id} className="rowlink" onClick={() => navigate(`/devices/${a.device_id}`)}>
                        <td><Badge tone={a.severity === "critical" ? "tone-red" : a.severity === "high" ? "tone-orange" : a.severity === "warning" ? "tone-amber" : "tone-green"}>{a.severity}</Badge></td>
                        <td style={{ fontWeight: 600, color: "var(--text)" }}>{a.device_name}</td>
                        <td>
                          <div>{a.kind}</div>
                          <div className="faint" style={{ fontSize: 12 }}>{a.message}</div>
                        </td>
                        <td className="num">{fmtTemp(a.temperature)}</td>
                        <td>{a.repeated ? <Badge tone="tone-amber">×{a.occurrences}</Badge> : <span className="faint">—</span>}</td>
                        <td>{fmtDate(a.occurred_at)}</td>
                        <td className="mono">{a.inspection_code ?? `#${a.inspection_id}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <button className="btn ghost small" disabled={page <= 1} onClick={() => setOffset((page - 2) * PAGE)}>← Prev</button>
                <span className="page-info">Page {page} of {pages}</span>
                <button className="btn ghost small" disabled={page >= pages} onClick={() => setOffset(page * PAGE)}>Next →</button>
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
