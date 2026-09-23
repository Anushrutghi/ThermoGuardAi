import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Report } from "../api/client";
import { Button, Card, EmptyState, ErrorState, LoadingState, RiskBadge, fmtDate, useToast } from "../components/ui";

interface ReportRow extends Report {
  deviceName?: string;
}

export default function Reports() {
  const navigate = useNavigate();
  const toast = useToast();
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .listReports()
      .then(async (list) => {
        // Enrich with the device name through the real inspection lookup.
        const rows: ReportRow[] = await Promise.all(
          list.map(async (r) => {
            if (r.inspection_id == null) return { ...r };
            try {
              const insp = await api.getInspection(r.inspection_id);
              return { ...r, deviceName: insp.device_name ?? undefined };
            } catch {
              return { ...r };
            }
          })
        );
        setReports(rows);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  const download = async (r: ReportRow) => {
    try {
      const blob = await api.downloadReport(r.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${r.title.replace(/[^\w.-]+/g, "_").slice(0, 60) || `report-${r.id}`}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast("success", "Report downloaded");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Download failed");
    }
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <p className="page-desc">Generated PDF inspection reports — downloads are authorization-protected.</p>
        </div>
      </div>

      {error && <ErrorState message={error} onRetry={load} />}
      {loading ? (
        <LoadingState label="Loading reports…" />
      ) : (
        <Card tight>
          {reports.length === 0 ? (
            <EmptyState
              icon="📄"
              title="No reports generated yet"
              body="Open an inspection and generate a PDF report from its real data."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Report</th>
                    <th>Device</th>
                    <th>Inspection</th>
                    <th>Risk</th>
                    <th>Generated</th>
                    <th>By</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {reports.map((r) => (
                    <tr key={r.id}>
                      <td style={{ fontWeight: 600, color: "var(--text)" }}>{r.title}</td>
                      <td>{r.deviceName ?? "—"}</td>
                      <td className="mono">#{r.inspection_id ?? "—"}</td>
                      <td>
                        <RiskBadge level={r.risk_score >= 75 ? "CRITICAL" : r.risk_score >= 50 ? "ABNORMAL" : r.risk_score >= 25 ? "ELEVATED" : "NORMAL"} />
                        <span className="faint" style={{ fontSize: 11, marginLeft: 6 }}>{r.risk_score.toFixed(0)}%</span>
                      </td>
                      <td>{fmtDate(r.generated_at)}</td>
                      <td>{r.generated_by ?? "—"}</td>
                      <td style={{ textAlign: "right" }}>
                        <div className="flex" style={{ justifyContent: "flex-end", gap: 8 }}>
                          <Button variant="ghost" size="small" onClick={() => navigate(`/inspections/${r.inspection_id}`)} disabled={r.inspection_id == null}>
                            View
                          </Button>
                          <Button variant="primary" size="small" onClick={() => download(r)}>
                            ⬇ PDF
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      <p className="disclaimer">Report generation and download require authorization; organization isolation is enforced by the backend.</p>
    </div>
  );
}
