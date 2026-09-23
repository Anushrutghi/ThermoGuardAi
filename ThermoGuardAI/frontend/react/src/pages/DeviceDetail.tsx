import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Device, type DeviceAnomaliesResponse, type DeviceDetail as DeviceDetailType, type DeviceMaintenanceResult, type HealthResult, type Inspection, type RiskResult, type ThermalHistoryItem, type TimelineEvent } from "../api/client";
import { ThermalLineChart } from "../components/charts";
import { Badge, Button, Card, EmptyState, ErrorState, EvidencePanel, Field, LoadingState, Modal, RiskBadge, StatusBadge, Tabs, ThermalMetric, ThermalSourceBadge, Timeline, fmtDate, fmtDelta, fmtTemp, pct, useToast } from "../components/ui";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "thermal", label: "Thermal History" },
  { key: "inspections", label: "Inspections" },
  { key: "anomalies", label: "Anomalies" },
  { key: "timeline", label: "Timeline" },
  { key: "maintenance", label: "Maintenance" },
];

export default function DeviceDetail() {
  const { id } = useParams();
  const deviceId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const [device, setDevice] = useState<DeviceDetailType | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("overview");
  const [editOpen, setEditOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .getDevice(deviceId)
      .then(setDevice)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deviceId]);
  useEffect(load, [load]);

  if (loading) return <LoadingState label="Loading device…" />;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!device) return <EmptyState title="Device not found" />;

  const history = device.history;

  return (
    <div>
      <div className="card">
        <div className="device-hero">
          <div className="dh-ident">
            <button className="btn ghost small mb-1" onClick={() => navigate("/devices")}>
              ← All devices
            </button>
            <div className="dh-name">{device.name}</div>
            <div className="dh-loc">
              {[device.location, device.building, device.floor, device.room].filter(Boolean).join(" · ") || "No location"}
              {device.manufacturer ? ` · ${device.manufacturer}${device.model ? ` ${device.model}` : ""}` : ""}
            </div>
            <div className="dh-badges">
              <StatusBadge status={device.derived_status} />
              <RiskBadge level={device.risk_level} />
              <ThermalSourceBadge simulated={device.last_thermal_simulated} />
            </div>
          </div>
          <HealthRing score={history?.current_health ?? null} />
          <div style={{ textAlign: "right" }}>
            <div className="flex" style={{ justifyContent: "flex-end" }}>
              <Button variant="ghost" size="small" onClick={() => setEditOpen(true)}>Edit</Button>
              <Button variant="danger" size="small" onClick={() => setDelOpen(true)}>Delete</Button>
            </div>
            <div className="mt-1" style={{ fontSize: 12, color: "var(--faint)" }}>
              {device.device_type.replace("_", " ")} · registered {fmtDate(device.created_at, false)}
            </div>
          </div>
        </div>
        {history?.simulated_excluded_from_temperatures && (
          <div className="warn-box mb-0 mt-1">
            DEMO / simulated thermal history exists for this device and is excluded from the analytics below by default.
          </div>
        )}
      </div>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      <TabBody device={device} tab={tab} />

      <EditModal device={device} open={editOpen} onClose={() => setEditOpen(false)} onSaved={(d) => { setDevice({ ...device, ...d }); toast("success", "Device updated"); }} />
      <DeleteModal device={device} open={delOpen} onClose={() => setDelOpen(false)} onDeleted={() => { toast("success", "Device deleted"); navigate("/devices"); }} />
    </div>
  );
}

function HealthRing({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return (
      <div style={{ textAlign: "center" }}>
        <div className="health-ring mid" style={{ ["--p" as string]: "0%", background: "var(--surface-3)" }}>
          <div className="hr-inner"><span className="hr-val">—</span></div>
        </div>
        <div className="hr-cap">No health data</div>
      </div>
    );
  }
  const tone = score >= 70 ? "good" : score >= 45 ? "mid" : "bad";
  return (
    <div style={{ textAlign: "center" }}>
      <div className={`health-ring ${tone}`} style={{ ["--p" as string]: `${Math.round(score)}%` }}>
        <div className="hr-inner"><span className="hr-val">{Math.round(score)}</span></div>
      </div>
      <div className="hr-cap">Device health*</div>
    </div>
  );
}

function TabBody({ device, tab }: { device: DeviceDetailType; tab: string }) {
  switch (tab) {
    case "overview":
      return <OverviewTab device={device} />;
    case "thermal":
      return <ThermalTab deviceId={device.id} />;
    case "inspections":
      return <InspectionsTab deviceId={device.id} recent={device.recent_inspections} />;
    case "anomalies":
      return <AnomaliesTab deviceId={device.id} />;
    case "timeline":
      return <TimelineTab deviceId={device.id} />;
    case "maintenance":
      return <MaintenanceTab deviceId={device.id} />;
    default:
      return <OverviewTab device={device} />;
  }
}

function OverviewTab({ device }: { device: DeviceDetailType }) {
  const h = device.history;
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [risk, setRisk] = useState<RiskResult | null>(null);
  const [comp, setComp] = useState<{ temperature_change: number | null; trend: string | null; insufficient: boolean; current: { temperature: number | null; thermal_delta: number | null } | null; previous: { temperature: number | null; thermal_delta: number | null } | null; message: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.getDeviceHealth(device.id), api.getDeviceRisk(device.id), api.getDeviceComparison(device.id)])
      .then(([h2, r, c]) => {
        setHealth(h2);
        setRisk(r);
        setComp(c);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [device.id]);

  if (h?.note) {
    return (
      <Card>
        <EmptyState icon="◷" title={h.note} body="This device has no completed inspections yet. Run a Live Inspection to start building its history." />
      </Card>
    );
  }
  if (error) return <ErrorState message={error} />;

  return (
    <div className="stack">
      <div className="metric-grid">
        <ThermalMetric label="Total inspections" value={h?.total_inspections ?? "—"} />
        <ThermalMetric label="Latest temp" value={h?.latest_temperature != null ? fmtTemp(h.latest_temperature) : "—"} tone={(h?.latest_temperature ?? 0) >= 50 ? "hot" : "mid"} />
        <ThermalMetric label="Max recorded" value={h?.max_temperature != null ? fmtTemp(h.max_temperature) : "—"} tone="hot" />
        <ThermalMetric label="Average temp" value={h?.avg_temperature != null ? fmtTemp(h.avg_temperature) : "—"} tone="mid" />
        <ThermalMetric label="Latest ΔT" value={h?.latest_thermal_delta != null ? fmtDelta(h.latest_thermal_delta) : "—"} tone={(h?.latest_thermal_delta ?? 0) > 15 ? "hot" : "mid"} />
        <ThermalMetric label="Anomalies" value={h?.anomaly_count ?? "—"} sub={h?.repeated_anomaly_kinds ? `${h.repeated_anomaly_kinds} repeated kind(s)` : "No repeats"} tone="mid" />
      </div>

      <div className="grid-2">
        <Card title="Health" sub="Explainable, deterministic — not a certified measurement">
          {loading ? <LoadingState label="Computing health…" /> : health?.insufficient ? (
            <EmptyState title={health.message} body="Health requires at least a few completed inspections." />
          ) : (
            <>
              <div className="flex mb-2">
                <Badge tone={(health?.score ?? 0) >= 70 ? "tone-green" : (health?.score ?? 0) >= 45 ? "tone-amber" : "tone-red"}>
                  {health?.rating ?? "—"}
                </Badge>
                <span className="num" style={{ fontSize: "1.3rem", fontWeight: 700 }}>{health?.score?.toFixed(0) ?? "—"}</span>
                <span className="muted" style={{ fontSize: 12 }}>/ 100</span>
              </div>
              <div className="row-list">
                {(health?.contributors ?? []).map((c) => (
                  <div key={c.name} className="row-item">
                    <div className="ri-main">
                      <div className="ri-title">{c.name.replace("_", " ")}</div>
                      <div className="ri-sub">{c.detail || "—"}</div>
                    </div>
                    <Badge tone={c.rating === "GOOD" ? "tone-green" : c.rating === "ATTENTION" ? "tone-amber" : c.rating === "CRITICAL" ? "tone-red" : "plain"}>{c.rating}</Badge>
                  </div>
                ))}
              </div>
              {health?.simulated_excluded ? (
                <p className="disclaimer mt-1">{health.simulated_excluded} simulated reading(s) excluded from this score.</p>
              ) : null}
              <p className="disclaimer mt-1">* {health?.disclaimer ?? "Heuristic indicator, not a scientifically validated engineering measurement."}</p>
            </>
          )}
        </Card>

        <Card title="Risk assessment" sub="Evidence-based level">
          {loading ? <LoadingState label="Assessing risk…" /> : risk?.insufficient ? (
            <EmptyState title={risk.message} body="Risk is computed from real inspection evidence." />
          ) : (
            <>
              <div className="flex mb-2">
                <RiskBadge level={risk?.level ?? null} />
                <span className="muted" style={{ fontSize: 12 }}>score {risk?.risk_score?.toFixed(0) ?? "—"}/100</span>
              </div>
              <EvidencePanel title="Evidence" items={risk?.evidence ?? []} />
              {risk?.reasoning && <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>{risk.reasoning}</p>}
            </>
          )}
        </Card>
      </div>

      {comp && !comp.insufficient && (
        <Card title="Inspection comparison" sub="Current vs previous valid inspection">
          <div className="metric-grid">
            <ThermalMetric label="Current temp" value={comp.current?.temperature != null ? fmtTemp(comp.current.temperature) : "—"} tone="mid" />
            <ThermalMetric label="Previous temp" value={comp.previous?.temperature != null ? fmtTemp(comp.previous.temperature) : "—"} tone="cool" />
            <ThermalMetric
              label="Change"
              value={comp.temperature_change != null ? `${comp.temperature_change >= 0 ? "+" : ""}${comp.temperature_change.toFixed(1)} °C` : "—"}
              tone={(comp.temperature_change ?? 0) > 2 ? "hot" : "mid"}
            />
            <ThermalMetric label="Trend" value={comp.trend ?? "—"} tone={(comp.trend ?? "") === "INCREASING" ? "hot" : "mid"} />
          </div>
          <p className="muted" style={{ fontSize: 12 }}>{comp.message}</p>
        </Card>
      )}

      {h?.maintenance_recommendation && (
        <Card title="Maintenance recommendation">
          <p style={{ fontSize: "0.9rem" }}>{h.maintenance_recommendation}</p>
        </Card>
      )}
    </div>
  );
}

function ThermalTab({ deviceId }: { deviceId: number }) {
  const [items, setItems] = useState<ThermalHistoryItem[]>([]);
  const [excludeDemo, setExcludeDemo] = useState(true);
  const [simExcluded, setSimExcluded] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    api
      .getDeviceThermalHistory(deviceId, { exclude_demo: excludeDemo, limit: 500 })
      .then((r) => {
        setItems(r.items);
        setSimExcluded(r.simulated_excluded);
        setNote(r.note ?? null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deviceId, excludeDemo]);

  const chartData = [...items]
    .sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
    .map((r) => ({
      timestamp: r.timestamp ?? "",
      device_name: "",
      inspection_id: r.inspection_id,
      temperature: r.max_temp,
      reference: r.reference_temp,
      delta: r.delta,
      simulated: r.simulated,
    }));

  return (
    <div className="stack">
      <div className="toolbar">
        <label className="flex" style={{ margin: 0, gap: 8 }}>
          <input type="checkbox" checked={excludeDemo} onChange={(e) => setExcludeDemo(e.target.checked)} />
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>Exclude DEMO / simulated readings from analytics</span>
        </label>
        {simExcluded > 0 && <span className="chip demo">{simExcluded} simulated reading(s) excluded</span>}
      </div>
      {error && <ErrorState message={error} />}
      <Card title="Temperature over time" sub="Real thermal readings per completed inspection">
      <ThermalLineChart
        data={chartData}
        series={[
          { key: "temperature", name: "Max temp", color: "var(--orange)" },
          { key: "reference", name: "Reference", color: "var(--cyan)", kind: "area" },
          { key: "delta", name: "ΔT", color: "var(--amber)" },
        ]}
        loading={loading}
        empty={!loading && items.length === 0}
        emptyTitle="No thermal history"
        emptyBody="Thermal readings appear after completed thermal scans."
        height={280}
      />
      {note && <p className="muted" style={{ fontSize: 12 }}>{note}</p>}
      </Card>
      <Card title="Per-inspection thermal summaries" tight>
        {items.length === 0 ? (
          <EmptyState title="No thermal history available" body="No completed thermal scans recorded for this device." />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Max</th>
                  <th>Avg</th>
                  <th>Reference</th>
                  <th>ΔT</th>
                  <th>Classification</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDate(r.timestamp)}</td>
                    <td className="num">{fmtTemp(r.max_temp)}</td>
                    <td className="num">{fmtTemp(r.avg_temp)}</td>
                    <td className="num">{fmtTemp(r.reference_temp)}</td>
                    <td className="num">{fmtDelta(r.delta)}</td>
                    <td><Badge tone={r.classification === "NORMAL" ? "tone-green" : r.classification === "ELEVATED" ? "tone-amber" : r.classification === "ABNORMAL" ? "tone-orange" : r.classification === "CRITICAL" ? "tone-red" : "plain"}>{r.classification ?? "—"}</Badge></td>
                    <td>{r.simulated ? <Badge tone="tone-amber">DEMO</Badge> : <Badge tone="tone-green">real</Badge>}</td>
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

function InspectionsTab({ deviceId, recent }: { deviceId: number; recent: Inspection[] }) {
  const [items, setItems] = useState<Inspection[]>(recent);
  const [total, setTotal] = useState(recent.length);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    setLoading(true);
    api
      .getDeviceInspections(deviceId, { limit: 200 })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [deviceId]);

  return (
    <Card title={`Inspections (${total})`} tight>
      {loading ? <LoadingState label="Loading inspections…" /> : items.length === 0 ? (
        <EmptyState title="No device-linked inspections" body="No inspections have been linked to this device yet." />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Code</th>
                <th>Mode</th>
                <th>Max temp</th>
                <th>Risk</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} className="rowlink" onClick={() => navigate(`/inspections/${i.id}`)}>
                  <td>{fmtDate(i.started_at)}</td>
                  <td className="mono">{i.inspection_code ?? `#${i.id}`}</td>
                  <td>{i.mode}</td>
                  <td className="num">{fmtTemp(i.temperature_stats?.max_temp ?? null)}</td>
                  <td>{pct(i.risk_score)}</td>
                  <td><Badge tone={i.status === "completed" ? "tone-green" : i.status === "running" ? "tone-blue" : "tone-gray"}>{i.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function AnomaliesTab({ deviceId }: { deviceId: number }) {
  const [data, setData] = useState<DeviceAnomaliesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .getDeviceAnomalies(deviceId, { limit: 200 })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deviceId]);

  return (
    <Card title="Anomaly history" sub="Frames within one inspection are never counted as separate anomalies">
      {error && <ErrorState message={error} />}
      {loading ? <LoadingState label="Loading anomalies…" /> : !data || data.total === 0 ? (
        <EmptyState title="No anomalies detected" body="No thermal anomalies have been recorded for this device." />
      ) : (
        <>
          <div className="flex mb-2">
            <Badge tone={data.repeated ? "tone-amber" : "tone-green"}>{data.repeated ? "Repeated anomaly detected" : "No repeats"}</Badge>
            <span className="muted" style={{ fontSize: 13 }}>{data.message}</span>
          </div>
          {data.kinds.map((k) => (
            <div key={k.fault_type} className="row-item" style={{ marginBottom: 8 }}>
              <span className="ri-main">
                <span className="ri-title">{k.kind}</span>
                <span className="ri-sub">found in {k.count} separate inspections</span>
              </span>
              <Badge tone="tone-amber">repeated ×{k.count}</Badge>
            </div>
          ))}
          <div className="table-wrap mt-2">
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Kind</th>
                  <th>Severity</th>
                  <th>Temp</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((a, i) => (
                  <tr key={i}>
                    <td>{fmtDate(a.occurred_at)}</td>
                    <td>{a.kind}</td>
                    <td><Badge tone={a.severity === "critical" ? "tone-red" : a.severity === "high" ? "tone-orange" : a.severity === "warning" ? "tone-amber" : "tone-green"}>{a.severity}</Badge></td>
                    <td className="num">{fmtTemp(a.temperature)}</td>
                    <td className="muted">{a.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

function TimelineTab({ deviceId }: { deviceId: number }) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .getDeviceTimeline(deviceId)
      .then((r) => setEvents(r.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deviceId]);

  if (error) return <ErrorState message={error} />;
  if (loading) return <LoadingState label="Loading timeline…" />;
  const toneOf = (t: string) => (t === "thermal_anomaly" || t === "alarm" || t === "incident" ? "alert" : t === "maintenance" ? "warn" : "ok");
  return (
    <Card title="Device timeline" sub="Real events from the database — never synthetic">
      <Timeline events={events.map((e) => ({ date: e.date, label: e.label, detail: e.detail, tone: toneOf(e.type) }))} />
    </Card>
  );
}

function MaintenanceTab({ deviceId }: { deviceId: number }) {
  const [data, setData] = useState<DeviceMaintenanceResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .getDeviceMaintenance(deviceId)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deviceId]);

  if (error) return <ErrorState message={error} />;
  if (loading) return <LoadingState label="Loading maintenance…" />;
  return (
    <div className="stack">
      <Card title="Recommendation" sub="Generated from actual inspection evidence">
        {data?.insufficient ? (
          <EmptyState title="Insufficient data" body="A recommendation requires real inspection history." />
        ) : (
          <>
            <div className="flex mb-2">
              <Badge tone="tone-blue">Basis</Badge>
              <span className="muted" style={{ fontSize: 13 }}>Risk: {data?.risk_level ?? "—"}</span>
            </div>
            <p style={{ fontSize: "0.95rem", fontWeight: 600 }}>{data?.recommendation ?? "Continue routine inspection."}</p>
            <EvidencePanel title="Why" items={data?.basis ?? []} />
          </>
        )}
      </Card>
      <Card title="Open work orders">
        {!data?.open_orders?.length ? (
          <EmptyState title="No open work orders" body="No pending or in-progress maintenance orders for this device." />
        ) : (
          <div className="row-list">
            {data.open_orders.map((o) => (
              <div key={o.id} className="row-item">
                <span className="ri-main">
                  <span className="ri-title mono">{o.code}</span>
                  <span className="ri-sub">{o.fault_type ?? "—"} · created {fmtDate(o.created_at, false)}</span>
                </span>
                <Badge tone={o.priority === "critical" ? "tone-red" : o.priority === "high" ? "tone-orange" : o.priority === "medium" ? "tone-amber" : "tone-gray"}>{o.priority}</Badge>
                <Badge tone={o.status === "pending" ? "tone-amber" : "tone-blue"}>{o.status}</Badge>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// ---- edit / delete ----
function EditModal({ device, open, onClose, onSaved }: { device: DeviceDetailType; open: boolean; onClose: () => void; onSaved: (d: Device) => void }) {
  const [name, setName] = useState(device.name);
  const [location, setLocation] = useState(device.location ?? "");
  const [manufacturer, setManufacturer] = useState(device.manufacturer ?? "");
  const [model, setModel] = useState(device.model ?? "");
  const [notes, setNotes] = useState(device.notes ?? "");
  const [status, setStatus] = useState(device.derived_status);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    setName(device.name);
    setLocation(device.location ?? "");
    setManufacturer(device.manufacturer ?? "");
    setModel(device.model ?? "");
    setNotes(device.notes ?? "");
    setStatus(device.derived_status);
  }, [device, open]);

  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const updated = await api.updateDevice(device.id, {
        name,
        location: location || undefined,
        manufacturer: manufacturer || undefined,
        model: model || undefined,
        notes: notes || undefined,
        derived_status: status,
      });
      onSaved(updated);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} title={`Edit ${device.name}`} onClose={onClose} footer={<Button variant="primary" busy={busy} onClick={save}>Save changes</Button>}>
      <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Location"><input value={location} onChange={(e) => setLocation(e.target.value)} /></Field>
      <div className="form-row">
        <Field label="Manufacturer"><input value={manufacturer} onChange={(e) => setManufacturer(e.target.value)} /></Field>
        <Field label="Model"><input value={model} onChange={(e) => setModel(e.target.value)} /></Field>
      </div>
      <Field label="Operational status" hint="MAINTENANCE / INACTIVE require administrator authorization; other values are re-derived from data.">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="ACTIVE">ACTIVE</option>
          <option value="MONITORING">MONITORING</option>
          <option value="ATTENTION">ATTENTION</option>
          <option value="HIGH_RISK">HIGH_RISK</option>
          <option value="CRITICAL">CRITICAL</option>
          <option value="MAINTENANCE">MAINTENANCE (manual)</option>
          <option value="INACTIVE">INACTIVE (manual)</option>
        </select>
      </Field>
      <Field label="Notes"><textarea value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      {err && <div className="error-box">{err}</div>}
    </Modal>
  );
}

function DeleteModal({ device, open, onClose, onDeleted }: { device: DeviceDetailType; open: boolean; onClose: () => void; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const del = async () => {
    setBusy(true);
    setErr("");
    try {
      await api.deleteDevice(device.id);
      onDeleted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title="Delete device" onClose={onClose} footer={<Button variant="danger" busy={busy} onClick={del}>Delete device</Button>}>
      <p>
        Delete <strong>{device.name}</strong>? This removes the device record. Existing inspection history linked to other records is preserved.
      </p>
      {err && <div className="error-box">{err}</div>}
    </Modal>
  );
}
