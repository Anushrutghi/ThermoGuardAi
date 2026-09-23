import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Device } from "../api/client";
import { IconPlus } from "../components/icons";
import { Button, Card, EmptyState, ErrorState, Field, LoadingState, Modal, RiskBadge, StatusBadge, fmtDate, fmtTemp, useToast } from "../components/ui";

const STATUS_FILTERS = ["ALL", "ACTIVE", "MONITORING", "ATTENTION", "HIGH_RISK", "CRITICAL", "MAINTENANCE", "INACTIVE"];

export default function Devices() {
  const navigate = useNavigate();
  const toast = useToast();
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("ALL");
  const [createOpen, setCreateOpen] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .listDevices()
      .then(setDevices)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    return devices.filter((d) => {
      if (status !== "ALL" && d.derived_status !== status) return false;
      if (!term) return true;
      return (
        d.name.toLowerCase().includes(term) ||
        (d.location ?? "").toLowerCase().includes(term) ||
        (d.manufacturer ?? "").toLowerCase().includes(term) ||
        (d.model ?? "").toLowerCase().includes(term)
      );
    });
  }, [devices, q, status]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    devices.forEach((d) => {
      c[d.derived_status] = (c[d.derived_status] ?? 0) + 1;
    });
    return c;
  }, [devices]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Devices</h1>
          <p className="page-desc">Every electrical device under inspection — status is derived from real inspection data.</p>
        </div>
        <div className="page-actions">
          <Button variant="primary" onClick={() => setCreateOpen(true)}>
            <IconPlus size={15} /> Register device
          </Button>
        </div>
      </div>

      <div className="toolbar">
        <div className="searchbox grow">
          <input placeholder="Search devices, locations, models…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search devices" />
        </div>
        <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 180 }}>
          {STATUS_FILTERS.map((s) => (
            <option key={s} value={s}>
              {s === "ALL" ? "All statuses" : `${s} (${counts[s] ?? 0})`}
            </option>
          ))}
        </select>
      </div>

      {error && <ErrorState message={error} onRetry={load} />}
      {loading && <LoadingState label="Loading devices…" />}

      {!loading && !error && (
        filtered.length === 0 ? (
          <Card>
            <EmptyState
              icon="◧"
              title={devices.length === 0 ? "No devices registered yet" : "No devices match your filters"}
              body={devices.length === 0 ? "Register your first electrical device, then run a Live Inspection against it." : "Try a different search term or status filter."}
              action={devices.length === 0 ? <Button variant="primary" onClick={() => setCreateOpen(true)}>Register device</Button> : undefined}
            />
          </Card>
        ) : (
          <div className="device-grid">
            {filtered.map((d) => (
              <DeviceCard key={d.id} device={d} onClick={() => navigate(`/devices/${d.id}`)} />
            ))}
          </div>
        )
      )}

      <Modal
        open={createOpen}
        title="Register a device"
        onClose={() => setCreateOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button>
          </>
        }
      >
        <CreateDeviceForm
          onCreated={(d) => {
            setDevices((list) => [d, ...list]);
            setCreateOpen(false);
            toast("success", `Device "${d.name}" registered`);
            navigate(`/devices/${d.id}`);
          }}
        />
      </Modal>
    </div>
  );
}

function DeviceCard({ device, onClick }: { device: Device; onClick: () => void }) {
  return (
    <div className="device-card" onClick={onClick} onKeyDown={(e) => { if (e.key === "Enter") onClick(); }} role="link" tabIndex={0}>
      <div className="dc-head">
        <div>
          <div className="dc-name">{device.name}</div>
          <div className="dc-loc">{(device.location ?? "No location") + (device.room ? ` · ${device.room}` : "")}</div>
        </div>
        <StatusBadge status={device.derived_status} />
      </div>
      <div className="dc-meta">
        <div>
          <div className="dc-temp">{device.last_max_temp != null ? fmtTemp(device.last_max_temp, 1) : "—"}</div>
          <div className="dc-last">Latest temp{device.last_thermal_simulated ? " (DEMO)" : ""}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <RiskBadge level={device.risk_level} />
          <div className="dc-last mt-1">{device.inspections_count} inspection{device.inspections_count === 1 ? "" : "s"} · last {fmtDate(device.last_inspection_at, false)}</div>
        </div>
      </div>
    </div>
  );
}

function CreateDeviceForm({ onCreated }: { onCreated: (d: Device) => void }) {
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [deviceType, setDeviceType] = useState("wall_switch");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const d = await api.createDevice({ name: name.trim(), location: location.trim() || undefined, device_type: deviceType });
      onCreated(d);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Create failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Field label="Device name" hint="e.g. Kitchen Switch, Main Distribution Board">
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Living Room Switch" required />
      </Field>
      <div className="form-row">
        <Field label="Device type">
          <select value={deviceType} onChange={(e) => setDeviceType(e.target.value)}>
            <option value="wall_switch">Wall switch</option>
            <option value="socket">Socket / outlet</option>
            <option value="breaker">Circuit breaker</option>
            <option value="panel">Panel / distribution board</option>
            <option value="motor">Motor</option>
            <option value="other">Other</option>
          </select>
        </Field>
        <Field label="Location">
          <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Building A · Floor 2 · Room 21" />
        </Field>
      </div>
      {err && <div className="error-box">{err}</div>}
      <Button variant="primary" block busy={busy} disabled={!name.trim()}>
        Register device
      </Button>
    </form>
  );
}
