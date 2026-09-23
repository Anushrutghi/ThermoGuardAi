import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type InspectionDetail as Detail } from "../api/client";
import { ThermalLineChart } from "../components/charts";
import { Badge, Button, Card, EmptyState, ErrorState, EvidencePanel, LoadingState, RiskBadge, ThermalMetric, ThermalSourceBadge, fmtDate, fmtTemp, pct, useToast } from "../components/ui";

export default function InspectionDetail() {
  const { id } = useParams();
  const inspectionId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const [inspection, setInspection] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .getInspection(inspectionId)
      .then(setInspection)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [inspectionId]);
  useEffect(load, [load]);

  const generateReport = async () => {
    if (!inspection) return;
    setBusy(true);
    try {
      const r = await api.generateReport(inspection.id, inspection.notes);
      toast("success", "PDF report generated");
      // refresh to show the report badge
      const fresh = await api.getInspection(inspection.id);
      setInspection(fresh);
      const blob = await api.downloadReport(r.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${r.title.replace(/[^\w.-]+/g, "_").slice(0, 60) || `report-${r.id}`}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Report generation failed");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <LoadingState label="Loading inspection…" />;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!inspection) return <EmptyState title="Inspection not found" />;

  const faults = inspection.faults ?? [];
  const ts = inspection.temperature_stats;
  const whyItems: string[] = [];
  faults.forEach((f) => {
    whyItems.push(`${f.message}${f.temperature != null ? ` (${fmtTemp(f.temperature)})` : ""}`);
  });
  if (ts?.max_temp != null && ts.max_temp > 40) whyItems.push(`Maximum temperature ${fmtTemp(ts.max_temp)} recorded during the session`);
  if (inspection.risk_score >= 60) whyItems.push(`Risk score ${inspection.risk_score.toFixed(0)}% — elevated risk level`);

  const chartData = (inspection.temperature_series ?? []).map((s) => ({
    timestamp: s.time,
    temperature: s.temp,
    label: s.label,
  }));

  return (
    <div>
      <div className="page-head">
        <div>
          <button className="btn ghost small mb-1" onClick={() => navigate("/inspections")}>← Inspections</button>
          <h1>{inspection.inspection_code ?? `Inspection #${inspection.id}`}</h1>
          <p className="page-desc">{fmtDate(inspection.started_at)} · {inspection.mode} mode · inspector {inspection.inspector ?? "—"}</p>
        </div>
        <div className="page-actions">
          <Button variant="ghost" size="small" onClick={() => navigate(`/devices/${inspection.device_id}`)} disabled={!inspection.device_id}>
            Open device
          </Button>
          <Button variant="primary" size="small" busy={busy} onClick={generateReport}>
            {inspection.report_generated_at ? "Regenerate PDF" : "Generate PDF report"}
          </Button>
        </div>
      </div>

      <div className="flex mb-2">
        <Badge tone={inspection.status === "completed" ? "tone-green" : inspection.status === "running" ? "tone-blue" : "tone-gray"}>{inspection.status}</Badge>
        <RiskBadge level={inspection.risk_score >= 75 ? "CRITICAL" : inspection.risk_score >= 50 ? "ABNORMAL" : inspection.risk_score >= 25 ? "ELEVATED" : "NORMAL"} />
        <span className="faint" style={{ fontSize: 12 }}>level derived from recorded risk score {inspection.risk_score.toFixed(0)}%</span>
        <ThermalSourceBadge simulated={inspection.thermal_simulated} source={inspection.thermal_source} />
        {inspection.report_generated_at && <Badge tone="tone-blue">PDF generated</Badge>}
      </div>

      {inspection.thermal_simulated && (
        <div className="warn-box">
          DEMO / SIMULATED THERMAL — this inspection used simulated thermal readings. Any temperatures shown are not from a physical thermal sensor.
        </div>
      )}

      <div className="metric-grid">
        <ThermalMetric label="Max temp" value={ts?.max_temp != null ? fmtTemp(ts.max_temp) : "—"} tone="hot" />
        <ThermalMetric label="Min temp" value={ts?.min_temp != null ? fmtTemp(ts.min_temp) : "—"} tone="cool" />
        <ThermalMetric label="Avg temp" value={ts?.avg_temp != null ? fmtTemp(ts.avg_temp) : "—"} tone="mid" />
        <ThermalMetric label="Risk score" value={pct(inspection.risk_score)} tone={(inspection.risk_score ?? 0) >= 50 ? "hot" : "mid"} />
        <ThermalMetric label="Health score" value={pct(inspection.health_score)} tone={(inspection.health_score ?? 0) >= 60 ? "cool" : "mid"} />
        <ThermalMetric label="Frames" value={inspection.frames_processed} sub={`${inspection.avg_fps?.toFixed(1) ?? "—"} fps`} />
        <ThermalMetric label="Duration" value={`${(inspection.duration_s ?? 0).toFixed(0)}s`} sub={`${inspection.component_count} components`} />
        <ThermalMetric label="Device" value={inspection.device_name ?? "—"} sub={inspection.device_location ?? ""} />
      </div>

      <div className="grid-2">
        <Card title="Why this result?" sub="Reasoning from the actual recorded evidence">
          {whyItems.length ? (
            <EvidencePanel title="Evidence recorded" items={whyItems} />
          ) : (
            <EmptyState title="No anomalies flagged" body="No faults or elevated temperatures were recorded during this inspection." />
          )}
        </Card>

        <Card title="Thermal / switch classification" sub="From the inspection pipeline">
          {faults.length ? (
            <div className="row-list">
              {faults.map((f, i) => (
                <div key={i} className="row-item">
                  <span className="ri-main">
                    <span className="ri-title">{f.fault_type.replace(/_/g, " ")}</span>
                    <span className="ri-sub">{f.message}</span>
                    {f.recommendation && <span className="ri-sub" style={{ color: "var(--amber)" }}>→ {f.recommendation}</span>}
                  </span>
                  <Badge tone={f.severity === "critical" ? "tone-red" : f.severity === "high" ? "tone-orange" : f.severity === "warning" ? "tone-amber" : "tone-green"}>{f.severity}</Badge>
                  <span className="num" style={{ fontFamily: "var(--num)" }}>{fmtTemp(f.temperature)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No faults classified — the session recorded a clean result.</p>
          )}
        </Card>
      </div>

      <Card title="Temperature timeline" sub="Real temperature readings captured during this session">
        {chartData.length ? (
          <ThermalLineChart data={chartData} series={[{ key: "temperature", name: "Temperature", color: "var(--orange)" }]} height={240} />
        ) : (
          <EmptyState title="No temperature series" body="No temperature readings were persisted for this inspection." />
        )}
      </Card>

      {inspection.detections?.length ? (
        <Card title="Switch detection" sub={`${inspection.detections.length} detection(s) recorded`}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Confidence</th>
                  <th>Temp</th>
                  <th>Health</th>
                </tr>
              </thead>
              <tbody>
                {inspection.detections.slice(-30).reverse().map((d, i) => (
                  <tr key={i}>
                    <td>{d.label}</td>
                    <td>{(d.confidence * 100).toFixed(0)}%</td>
                    <td className="num">{fmtTemp(d.temperature)}</td>
                    <td><Badge tone={d.health === "healthy" ? "tone-green" : d.health === "warning" ? "tone-amber" : d.health === "high" ? "tone-orange" : "tone-red"}>{d.health}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {inspection.incidents?.length ? (
        <Card title="Incident reports" sub="Automatically generated for critical findings">
          {inspection.incidents.map((inc) => (
            <div key={inc.id} className="row-item mb-1">
              <span className="ri-main">
                <span className="ri-title mono">{inc.code}</span>
                <span className="ri-sub">{inc.fault_type} · {inc.suggested_action ?? "No suggested action"}</span>
              </span>
              <Badge tone="tone-red">{inc.severity}</Badge>
            </div>
          ))}
        </Card>
      ) : null}
    </div>
  );
}
