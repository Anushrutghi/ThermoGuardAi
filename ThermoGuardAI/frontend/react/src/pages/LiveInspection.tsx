import { useCallback, useEffect, useRef, useState } from "react";
import { api, wsUrl, type Device, type SwitchStatus, type ThermalStatus } from "../api/client";
import { Badge, Button, Card, EmptyState, EvidencePanel, Progress, ThermalMetric, ThermalStatusBadge, cls, useToast } from "../components/ui";

const WORKFLOW = [
  { state: "SEARCHING_FOR_SWITCH", label: "Searching" },
  { state: "SWITCH_DETECTED", label: "Detected" },
  { state: "SWITCH_STABLE", label: "Stable" },
  { state: "INSPECTION_REGION_LOCKED", label: "Region locked" },
  { state: "THERMAL_INITIALIZING", label: "Thermal init" },
  { state: "THERMAL_BASELINE", label: "Baseline" },
  { state: "THERMAL_SCANNING", label: "Scanning" },
  { state: "HOTSPOT_ANALYSIS", label: "Hotspot" },
  { state: "THERMAL_TREND_ANALYSIS", label: "Trend" },
  { state: "ANOMALY_ANALYSIS", label: "Anomaly" },
  { state: "RISK_ASSESSMENT", label: "Risk" },
  { state: "INSPECTION_COMPLETE", label: "Complete" },
];

export default function LiveInspection() {
  const toast = useToast();
  // ---- device + camera selection (S1, unchanged) ----
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newLocation, setNewLocation] = useState("");
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [cameraId, setCameraId] = useState<string>("");

  // ---- session state ----
  const [inspectionId, setInspectionId] = useState<number | null>(null);
  const [conn, setConn] = useState<"idle" | "connecting" | "on" | "off">("idle");
  const [status, setStatus] = useState<SwitchStatus | null>(null);
  const [alarms, setAlarms] = useState<{ severity: string; message: string }[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [cameraError, setCameraError] = useState<string | null>(null);

  // S5: authoritative backend thermal status — the backend decides REAL vs
  // DEMO/SIMULATED; the UI renders exactly what /thermal/status reports.
  const [thermalStatus, setThermalStatus] = useState<ThermalStatus | null>(null);
  const [thermalStatusError, setThermalStatusError] = useState<string | null>(null);

  // S5: refresh the authoritative thermal status on mount, when a session
  // ends, and on retry — hardware can appear/disappear at any time.
  const refreshThermalStatus = useCallback(() => {
    api
      .thermalStatus()
      .then((t) => {
        setThermalStatus(t);
        setThermalStatusError(null);
      })
      .catch((e) => {
        setThermalStatusError((e as Error).message);
        setThermalStatus(null);
      });
  }, []);

  useEffect(() => {
    refreshThermalStatus();
    const timer = window.setInterval(() => refreshThermalStatus(), 15000);
    return () => window.clearInterval(timer);
  }, [refreshThermalStatus]);

  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef(0);
  const lastSendRef = useRef(0);
  const inFlightRef = useRef(false);
  const intervalRef = useRef(66);
  const rttRef = useRef(66);
  const statusRef = useRef<SwitchStatus | null>(null);
  const doneRef = useRef(false);

  const log = (msg: string) => setLogs((l) => [msg, ...l].slice(0, 30));

  // ------------------------------------------------------------------
  // Device + camera discovery
  // ------------------------------------------------------------------
  useEffect(() => {
    api
      .listDevices()
      .then((d) => {
        setDevices(d);
        if (d.length > 0) setDeviceId(d[0].id);
      })
      .catch((e) => log("Devices unavailable: " + e.message));
    navigator.mediaDevices
      ?.enumerateDevices()
      .then((list) => {
        const cams = list.filter((d) => d.kind === "videoinput");
        setCameras(cams);
        const preferred = cams.find((c) => /back|rear|environment/i.test(c.label)) ?? cams[cams.length - 1];
        setCameraId(preferred?.deviceId ?? "");
      })
      .catch(() => log("Camera enumeration unavailable"));
  }, []);

  const createDevice = async () => {
    if (!newName.trim()) return;
    try {
      const d = await api.createDevice({ name: newName.trim(), location: newLocation.trim() || undefined, device_type: "wall_switch" });
      setDevices((list) => [...list, d]);
      setDeviceId(d.id);
      setShowCreate(false);
      setNewName("");
      setNewLocation("");
      log(`Device created: ${d.name}`);
      toast("success", `Device "${d.name}" created`);
    } catch (e) {
      log("Create device failed: " + (e as Error).message);
    }
  };

  const releaseCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  // ------------------------------------------------------------------
  // Live loop: send frames + redraw the switch overlay (S1, unchanged)
  // ------------------------------------------------------------------
  const drawOverlay = (video: HTMLVideoElement) => {
    const canvas = overlayRef.current;
    const st = statusRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    canvas.width = video.clientWidth;
    canvas.height = video.clientHeight;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!st || video.videoWidth === 0) return;
    const sx = canvas.width / video.videoWidth;
    const sy = canvas.height / video.videoHeight;
    if (st.roi) {
      const [x1, y1, x2, y2] = st.roi;
      ctx.strokeStyle = "#d4c13c";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
      ctx.setLineDash([]);
      ctx.font = "600 11px Inter, system-ui, sans-serif";
      ctx.fillStyle = "rgba(20,20,10,0.85)";
      const rl = "INSPECTION REGION";
      ctx.fillRect(x1 * sx, Math.max(0, y1 * sy - 18), ctx.measureText(rl).width + 10, 16);
      ctx.fillStyle = "#d4c13c";
      ctx.fillText(rl, x1 * sx + 5, Math.max(11, y1 * sy - 6));
    }
    if (st.switch_bbox) {
      const [x1, y1, x2, y2] = st.switch_bbox;
      const confirmed = st.state === "SWITCH_STABLE" || WORKFLOW.some((w) => w.state === st.state && w.state !== "SWITCH_DETECTED");
      ctx.strokeStyle = confirmed ? "#3ddc84" : "#35c8e8";
      ctx.lineWidth = 3;
      ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
      const label = `SWITCH ${(st.switch_confidence * 100).toFixed(0)}%${confirmed ? " ✓" : ""}`;
      ctx.font = "600 13px Inter, system-ui, sans-serif";
      const tw = ctx.measureText(label).width + 12;
      ctx.fillStyle = confirmed ? "#1d7a45" : "#12708a";
      ctx.fillRect(x1 * sx, Math.max(0, y1 * sy - 22), tw, 20);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x1 * sx + 6, Math.max(13, y1 * sy - 7));
    }
  };

  const loop = () => {
    const video = videoRef.current;
    if (video && video.videoWidth > 0) drawOverlay(video);
    if (!video || video.videoWidth === 0) {
      rafRef.current = requestAnimationFrame(loop);
      return;
    }
    const now = performance.now();
    if (inFlightRef.current && now - lastSendRef.current > 2000) {
      inFlightRef.current = false;
      intervalRef.current = Math.min(250, intervalRef.current * 1.3);
    }
    if (
      !doneRef.current &&
      !inFlightRef.current &&
      now - lastSendRef.current >= intervalRef.current &&
      wsRef.current?.readyState === WebSocket.OPEN
    ) {
      lastSendRef.current = now;
      inFlightRef.current = true;
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")!.drawImage(video, 0, 0);
      const b64 = canvas.toDataURL("image/jpeg", 0.72).split(",")[1];
      wsRef.current.send(JSON.stringify({ type: "frame", jpeg_base64: b64 }));
    }
    rafRef.current = requestAnimationFrame(loop);
  };

  // ------------------------------------------------------------------
  // Start / stop
  // ------------------------------------------------------------------
  const start = async () => {
    if (deviceId === null) {
      log("Select or create a device first");
      return;
    }
    setCameraError(null);
    setConn("connecting");
    try {
      const video: MediaTrackConstraints = { width: { ideal: 1280 }, height: { ideal: 720 } };
      if (cameraId) video.deviceId = { exact: cameraId };
      else video.facingMode = "environment";
      streamRef.current = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      streamRef.current.getTracks().forEach((t) =>
        t.addEventListener("ended", () => {
          log("Camera disconnected — permission revoked or device removed");
          setConn("off");
          toast("error", "Camera connection lost");
          releaseCamera();
        })
      );
      if (videoRef.current) {
        videoRef.current.srcObject = streamRef.current;
        await videoRef.current.play();
      }
    } catch (e) {
      const msg = (e as Error).message || "Camera access failed";
      setCameraError(msg);
      setConn("idle");
      log("Camera denied: " + msg);
      return;
    }

    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;
    doneRef.current = false;
    ws.onopen = () => {
      setConn("on");
      ws.send(JSON.stringify({ type: "start_inspection", mode: "switch_first", device_id: deviceId }));
      log("Connected — pointing the camera at a wall switch…");
      loop();
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "ready") {
        setInspectionId(msg.inspection_id);
        log(`Inspection started #${msg.inspection_id}`);
      } else if (msg.type === "switch_status") {
        const rtt = performance.now() - lastSendRef.current;
        rttRef.current = rttRef.current === 0 ? rtt : 0.8 * rttRef.current + 0.2 * rtt;
        if (rttRef.current > intervalRef.current * 1.6) intervalRef.current = Math.min(250, intervalRef.current * 1.25);
        else if (rttRef.current < intervalRef.current * 0.55) intervalRef.current = Math.max(33, intervalRef.current * 0.8);
        inFlightRef.current = false;
        statusRef.current = msg.data;
        setStatus(msg.data);
        if (msg.data.complete) {
          doneRef.current = true;
          toast("info", "Inspection complete — results saved");
        }
      } else if (msg.type === "switch_complete") {
        log("✓ Inspection complete — results saved");
      } else if (msg.type === "alarm") {
        setAlarms((a) => [{ severity: msg.severity, message: msg.message }, ...a].slice(0, 5));
      } else if (msg.type === "error") {
        log("Error: " + msg.message);
      }
    };
    ws.onclose = () => {
      setConn((c) => (c === "on" ? "off" : c));
      if (!doneRef.current) log("Disconnected — reconnecting requires a retry");
      releaseCamera();
    };
    ws.onerror = () => {
      setConn("off");
      log("WebSocket error — connection failed");
    };
  };

  const stop = () => {
    cancelAnimationFrame(rafRef.current);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop_inspection" }));
      wsRef.current.close();
    }
    wsRef.current = null;
    setConn("idle");
    setStatus(null);
    setInspectionId(null);
    releaseCamera();
  };

  useEffect(() => () => stop(), []); // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  const s = status;
  const guidance = s?.guidance;
  const complete = s?.complete;
  const stateIdx = s ? WORKFLOW.findIndex((w) => w.state === s.state) : -1;
  const active = conn === "on";

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Live Electrical Inspection</h1>
          <p className="page-desc">Switch-first guided inspection · point the camera at a wall switch</p>
        </div>
        <div className="page-actions">
          <span className={cls("conn-pill", conn === "on" ? "on" : conn === "connecting" || conn === "idle" ? "busy" : "off")}>
            <span className="dot" />
            {conn === "on" ? "Live" : conn === "connecting" ? "Connecting…" : conn === "off" ? "Disconnected" : "Ready"}
          </span>
          {!active ? (
            <Button variant="primary" size="big" onClick={start} disabled={deviceId === null}>
              ▶ Start inspection
            </Button>
          ) : (
            <Button variant="danger" size="big" onClick={stop}>
              ■ Finish
            </Button>
          )}
        </div>
      </div>

      {/* Setup */}
      {!active && (
        <Card className="setup-card">
          <div className="grid-2">
            <div>
              <h3>1 · Device</h3>
              {devices.length > 0 ? (
                <select value={deviceId ?? ""} onChange={(e) => setDeviceId(Number(e.target.value))}>
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                      {d.location ? ` — ${d.location}` : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <EmptyState title="No devices yet" body="Create the first device to inspect." />
              )}
              <button className="btn ghost small" onClick={() => setShowCreate((v) => !v)} style={{ marginTop: 6 }}>
                {showCreate ? "Cancel" : "+ New device"}
              </button>
              {showCreate && (
                <div className="form-row" style={{ marginTop: 8 }}>
                  <input placeholder="Device name" value={newName} onChange={(e) => setNewName(e.target.value)} />
                  <input placeholder="Location (optional)" value={newLocation} onChange={(e) => setNewLocation(e.target.value)} />
                </div>
              )}
              {showCreate && (
                <Button size="small" onClick={createDevice} disabled={!newName.trim()} className="mt-1">
                  Create
                </Button>
              )}
            </div>
            <div>
              <h3>2 · Thermal sensor</h3>
              <div className="flex spread" style={{ alignItems: "center" }}>
                <ThermalStatusBadge status={thermalStatus} />
                <button className="btn ghost small" onClick={refreshThermalStatus}>↻ Refresh</button>
              </div>
              {thermalStatusError && <div className="error-box">Thermal status unavailable: {thermalStatusError}</div>}
              {thermalStatus?.simulated && (
                <p className="muted" style={{ fontSize: 13, marginTop: 6 }}>
                  DEMO / SIMULATED THERMAL — no physical sensor detected. Temperatures in this session are simulated and excluded from real analytics by default.
                </p>
              )}
              {thermalStatus && !thermalStatus.simulated && thermalStatus.connected && (
                <p className="muted" style={{ fontSize: 13, marginTop: 6 }}>
                  Real sensor {thermalStatus.source || ""} · {thermalStatus.metadata?.model || "model unknown"} · {thermalStatus.metadata?.resolution || "res unknown"} · emissivity {thermalStatus.emissivity ?? "n/a"}
                </p>
              )}
              {thermalStatus && !thermalStatus.simulated && !thermalStatus.connected && (
                <p className="muted" style={{ fontSize: 13, marginTop: 6 }}>
                  THERMAL SENSOR UNAVAILABLE — connect a supported sensor (MLX90640, Lepton, AMG8833, Seek) or run in DEMO mode.
                </p>
              )}
            </div>
            <div>
              <h3>3 · Camera</h3>
              {cameras.length > 1 ? (
                <select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
                  {cameras.map((c) => (
                    <option key={c.deviceId} value={c.deviceId}>
                      {c.label || "Camera"}
                    </option>
                  ))}
                </select>
              ) : (
                <p className="muted">{cameras.length === 1 ? `Using: ${cameras[0].label || "default camera"}` : "Camera is requested when you start."}</p>
              )}
              {cameraError && <div className="error-box">Camera permission denied or unavailable: {cameraError}</div>}
              {conn === "off" && (
                <div className="warn-box mt-1">
                  Connection lost. Camera and inspection session have stopped — press <strong>Start inspection</strong> to retry.
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* Workflow stepper */}
      {s && (
        <Card tight>
          <div className="stepper" role="list" aria-label="Inspection workflow">
            {WORKFLOW.map((w, i) => (
              <span key={w.state} className="flex" style={{ gap: 4 }}>
                {i > 0 && <span className="step-arrow">›</span>}
                <span
                  className={cls(
                    "step",
                    i < stateIdx || (stateIdx === WORKFLOW.length - 1 && i === WORKFLOW.length - 1) ? "done" : "",
                    i === stateIdx && stateIdx < WORKFLOW.length - 1 ? "active" : ""
                  )}
                  role="listitem"
                >
                  <span className="s-num">{i + 1}</span>
                  {w.label}
                </span>
              </span>
            ))}
          </div>
        </Card>
      )}

      <div className="live-layout">
        {/* Camera */}
        <div className="stack">
          <div className="video-frame">
            <div className="video-badge">
              {active && <span className="badge tone-red"><span className="rec-dot" /> LIVE</span>}
              {inspectionId && <Badge tone="tone-blue">Inspection #{inspectionId}</Badge>}
              {/* S5: authoritative backend source — never inferred client-side */}
              <ThermalStatusBadge status={thermalStatus} />
            </div>
            <video ref={videoRef} autoPlay playsInline muted className="video" />
            <canvas ref={overlayRef} className="video-overlay" />
            {!streamRef.current && <div className="video-empty">Start inspection to activate your camera</div>}
          </div>

          {s?.thermal_simulated && (
            <div className="warn-box mb-0">
              <strong>DEMO / SIMULATED THERMAL.</strong> No physical thermal sensor is connected — temperatures in this session are simulated and are excluded from real analytics by default.
            </div>
          )}

          {/* Guidance */}
          {s && (
            <Card className="guidance-card" title="Camera guidance" sub="Position the switch inside the box and hold steady">
              <div className="g-score">
                <span className="g-num">{guidance ? `${guidance.score}%` : "—"}</span>
                <Progress value={guidance?.score ?? 0} sub={guidance?.ready ? "ready" : "not ready"} />
              </div>
              <div style={{ marginTop: 10 }}>
                {(guidance?.messages ?? []).map((m, i) => (
                  <div key={i} className="g-msg">{m}</div>
                ))}
                {s.switch_bbox && !s.complete && (
                  <div className="flex mt-1">
                    <Badge tone="tone-blue">Detection {(s.switch_confidence * 100).toFixed(0)}%</Badge>
                    <Badge tone="tone-green">Stability {s.stable_frames}/{s.stable_required} frames</Badge>
                  </div>
                )}
              </div>
            </Card>
          )}

          {/* Session log */}
          <Card title="Session log" tight>
            <pre className="log">{logs.join("\n") || "Waiting…"}</pre>
          </Card>
        </div>

        {/* Right column */}
        <div className="side-column" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {/* Thermal readout */}
          <Card title="Thermal readout" sub={s?.thermal_stage ? `Stage: ${s.thermal_stage}` : undefined}>
            <div className="thermal-grid">
              <ThermalMetric label="Switch" value={s?.scan_sample ? `${s.scan_sample.switch.toFixed(1)}°` : "—"} tone="mid" />
              <ThermalMetric label="Wall ref" value={s?.scan_sample ? `${s.scan_sample.wall.toFixed(1)}°` : "—"} tone="cool" />
              <ThermalMetric
                label="ΔT"
                value={s?.scan_sample && s.scan_sample.switch != null && s.scan_sample.wall != null ? `${(s.scan_sample.switch - s.scan_sample.wall).toFixed(1)}°` : "—"}
                tone={s?.scan_sample && s.scan_sample.switch - s.scan_sample.wall > 15 ? "hot" : "mid"}
              />
              <ThermalMetric label="Max" value={complete?.max_temp != null ? `${complete.max_temp.toFixed(1)}°` : "—"} tone="hot" />
              <ThermalMetric label="Classification" value={complete?.classification ?? "—"} />
              <ThermalMetric label="Risk" value={complete ? `${complete.risk_score.toFixed(0)}%` : "—"} tone={(complete?.risk_score ?? 0) >= 50 ? "hot" : "mid"} />
            </div>
            <div className="mt-1">
              {s?.thermal_stage === "baseline" && <Progress value={(s.baseline_frames / Math.max(1, s.baseline_required)) * 100} label="Baseline" sub={`${s.baseline_frames}/${s.baseline_required}`} />}
              {s?.thermal_stage === "scanning" && <Progress value={(s.scan_frames / Math.max(1, s.scan_required)) * 100} label="Scanning" sub={`${s.scan_frames}/${s.scan_required}`} />}
              {s && !s.complete && <Progress value={s.progress} label="Overall" sub={`${s.progress}%`} />}
            </div>
          </Card>

          {/* Results */}
          {complete && (
            <>
              <div className={cls("result-hero", complete.classification.toLowerCase())}>
                <div className="rh-level">{complete.classification}</div>
                <div className="rh-msg">{complete.message}</div>
                <div className="flex" style={{ marginTop: 8 }}>
                  <Badge tone={complete.classification === "NORMAL" ? "tone-green" : complete.classification === "ELEVATED" ? "tone-amber" : complete.classification === "ABNORMAL" ? "tone-orange" : "tone-red"}>
                    Risk {complete.risk_score.toFixed(0)}%
                  </Badge>
                  {complete.rapid_increase && <Badge tone="tone-red">Rapid increase</Badge>}
                </div>
              </div>

              <Card title="Thermal results">
                <div className="thermal-grid">
                  <ThermalMetric label="MAX" value={complete.max_temp != null ? `${complete.max_temp.toFixed(1)}°` : "—"} tone="hot" />
                  <ThermalMetric label="REFERENCE" value={complete.wall_temp != null ? `${complete.wall_temp.toFixed(1)}°` : "—"} tone="cool" />
                  <ThermalMetric label="DELTA" value={complete.delta_vs_wall != null ? `+${complete.delta_vs_wall.toFixed(1)}°` : "—"} tone="hot" />
                  <ThermalMetric label="HOTSPOT" value={complete.hotspot ? `${complete.hotspot.x.toFixed(0)},${complete.hotspot.y.toFixed(0)}` : "—"} sub={complete.heat_path ? complete.heat_path.replace(/_/g, " ") : ""} />
                  <ThermalMetric label="TREND" value={complete.trend_c_per_min != null ? `${complete.trend_c_per_min >= 0 ? "+" : ""}${complete.trend_c_per_min.toFixed(1)}°/min` : "—"} tone={complete.rapid_increase ? "hot" : "mid"} />
                  <ThermalMetric label="Switch temp" value={complete.switch_temp != null ? `${complete.switch_temp.toFixed(1)}°` : "—"} tone="mid" />
                </div>
                <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
                  Surface thermal pattern: {complete.heat_path.replace(/_/g, " ")}. The system measures surface temperature distribution — it cannot see through walls.
                </p>
              </Card>

              <Card title="WHY THIS RESULT?">
                <EvidencePanel title="Evidence recorded" items={complete.evidence} />
              </Card>
            </>
          )}

          {alarms.length > 0 && (
            <Card title="Alarms" tight>
              {alarms.map((a, i) => (
                <div key={i} className="row-item" style={{ marginBottom: 6 }}>
                  <Badge tone={a.severity === "critical" ? "tone-red" : a.severity === "high" ? "tone-orange" : "tone-amber"}>{a.severity}</Badge>
                  <span style={{ fontSize: 13 }}>{a.message}</span>
                </div>
              ))}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
