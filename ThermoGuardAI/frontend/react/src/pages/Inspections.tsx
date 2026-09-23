import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type Inspection, type Report } from "../api/client";
import { Badge, Card, EmptyState, ErrorState, LoadingState, pct, fmtDate, fmtTemp } from "../components/ui";

const PAGE = 20;

export default function Inspections() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [items, setItems] = useState<Inspection[]>([]);
  const [total, setTotal] = useState(0);
  const [reports, setReports] = useState<Record<number, Report>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState(searchParams.get("q") ?? "");
  const [status, setStatus] = useState("");
  const [deviceFilter, setDeviceFilter] = useState("");
  const [sort, setSort] = useState("started_at");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [offset, setOffset] = useState(0);
  const [devices, setDevices] = useState<{ id: number; name: string }[]>([]);

  useEffect(() => {
    api.listDevices().then((d) => setDevices(d.map((x) => ({ id: x.id, name: x.name })))).catch(() => undefined);
    api.listReports().then((r) => {
      const map: Record<number, Report> = {};
      r.forEach((rep) => {
        if (rep.inspection_id != null) map[rep.inspection_id] = rep;
      });
      setReports(map);
    }).catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .listInspections({
        search: q || undefined,
        status: status || undefined,
        device_id: deviceFilter ? Number(deviceFilter) : undefined,
        sort,
        order,
        limit: PAGE,
        offset,
      })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [q, status, deviceFilter, sort, order, offset]);
  useEffect(load, [load]);

  const pages = Math.max(1, Math.ceil(total / PAGE));
  const page = Math.floor(offset / PAGE) + 1;

  const toggleSort = (key: string) => {
    if (sort === key) setOrder((o) => (o === "desc" ? "asc" : "desc"));
    else {
      setSort(key);
      setOrder("desc");
    }
    setOffset(0);
  };

  const sortArrow = (key: string) => (sort === key ? (order === "desc" ? " ↓" : " ↑") : "");

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Inspection History</h1>
          <p className="page-desc">{total} inspection{total === 1 ? "" : "s"} · real database records</p>
        </div>
      </div>

      <div className="toolbar">
        <div className="searchbox grow">
          <input placeholder="Search code, inspector, panel…" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} aria-label="Search inspections" />
        </div>
        <select aria-label="Filter by status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} style={{ width: 150 }}>
          <option value="">All statuses</option>
          <option value="completed">Completed</option>
          <option value="running">Running</option>
          <option value="aborted">Aborted</option>
        </select>
        <select aria-label="Filter by device" value={deviceFilter} onChange={(e) => { setDeviceFilter(e.target.value); setOffset(0); }} style={{ width: 190 }}>
          <option value="">All devices</option>
          {devices.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
        <select aria-label="Sort by" value={sort} onChange={(e) => { setSort(e.target.value); setOffset(0); }} style={{ width: 170 }}>
          <option value="started_at">Sort: date</option>
          <option value="risk_score">Sort: risk</option>
          <option value="max_temp">Sort: max temp</option>
          <option value="health_score">Sort: health</option>
        </select>
      </div>

      {error && <ErrorState message={error} onRetry={load} />}
      {loading ? (
        <LoadingState label="Loading inspections…" />
      ) : (
        <Card tight>
          {items.length === 0 ? (
            <EmptyState
              icon="🗂"
              title={total === 0 ? "No inspections yet" : "No inspections match your filters"}
              body={total === 0 ? "Run a Live Inspection or start a session to create the first record." : "Try different filters or a broader search."}
            />
          ) : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th className="sortable" onClick={() => toggleSort("started_at")}>Date{sortArrow("started_at")}</th>
                      <th>Device</th>
                      <th>Thermal</th>
                      <th className="sortable" onClick={() => toggleSort("max_temp")}>Max temp{sortArrow("max_temp")}</th>
                      <th className="sortable" onClick={() => toggleSort("risk_score")}>Risk{sortArrow("risk_score")}</th>
                      <th>Faults</th>
                      <th>Status</th>
                      <th>Report</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((i) => (
                      <tr key={i.id} className="rowlink" onClick={() => navigate(`/inspections/${i.id}`)}>
                        <td>{fmtDate(i.started_at)}</td>
                        <td style={{ fontWeight: 600, color: "var(--text)" }}>{i.device_name ?? "—"}</td>
                        <td>
                          {i.thermal_simulated ? <Badge tone="tone-amber">DEMO</Badge> : i.thermal_source ? <Badge tone="tone-green">real</Badge> : <Badge tone="plain">—</Badge>}
                        </td>
                        <td className="num">{fmtTemp(i.temperature_stats?.max_temp ?? null)}</td>
                        <td>{pct(i.risk_score)}</td>
                        <td>{i.faults_count ?? 0}</td>
                        <td><Badge tone={i.status === "completed" ? "tone-green" : i.status === "running" ? "tone-blue" : "tone-gray"}>{i.status}</Badge></td>
                        <td>{reports[i.id] ? <Badge tone="tone-blue">✓ PDF</Badge> : <span className="faint">—</span>}</td>
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
