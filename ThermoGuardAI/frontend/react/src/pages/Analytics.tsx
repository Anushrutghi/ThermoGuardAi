import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Device, type ThermalHistoryItem, type TrendResult } from "../api/client";
import { BarsChart, ChartCard, ThermalLineChart } from "../components/charts";
import { Badge, Card, EmptyState, ErrorState, LoadingState, fmtDate, fmtTemp } from "../components/ui";

export default function Analytics() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<number | 0>(0);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [excludeDemo, setExcludeDemo] = useState(true);
  const [thermal, setThermal] = useState<ThermalHistoryItem[]>([]);
  const [simExcluded, setSimExcluded] = useState(0);
  const [trend, setTrend] = useState<TrendResult | null>(null);
  const [deltaTrend, setDeltaTrend] = useState<TrendResult | null>(null);
  const [daily, setDaily] = useState<{ date: string; count: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listDevices()
      .then((d) => setDevices(d))
      .catch(() => undefined);
    api.inspectionsPerDay(90).then((r) => setDaily(r.items)).catch(() => undefined);
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    if (!deviceId) {
      setThermal([]);
      setTrend(null);
      setDeltaTrend(null);
      setLoading(false);
      return;
    }
    Promise.all([
      api.getDeviceThermalHistory(deviceId, { exclude_demo: excludeDemo, limit: 500 }),
      api.getDeviceTemperatureTrend(deviceId, excludeDemo),
      api.getDeviceDeltaTrend(deviceId, excludeDemo),
    ])
      .then(([t, tr, dt]) => {
        setThermal(t.items);
        setSimExcluded(t.simulated_excluded);
        setTrend(tr);
        setDeltaTrend(dt);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [deviceId, excludeDemo]);
  useEffect(load, [load]);

  const inRange = (iso: string | null) => {
    if (!iso) return true;
    if (fromDate && iso.slice(0, 10) < fromDate) return false;
    if (toDate && iso.slice(0, 10) > toDate) return false;
    return true;
  };

  const chartData = useMemo(
    () =>
      thermal
        .filter((r) => inRange(r.timestamp))
        .sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
        .map((r) => ({
          timestamp: r.timestamp ?? "",
          temperature: r.max_temp,
          reference: r.reference_temp,
          delta: r.delta,
          inspection_id: r.inspection_id,
          simulated: r.simulated,
        })),
    [thermal, fromDate, toDate] // eslint-disable-line react-hooks/exhaustive-deps
  );

  const deltaData = useMemo(
    () =>
      (deltaTrend?.points ?? [])
        .filter((p) => inRange(p.timestamp))
        .map((p) => ({ timestamp: p.timestamp ?? "", delta: p.delta ?? null })),
    [deltaTrend, fromDate, toDate] // eslint-disable-line react-hooks/exhaustive-deps
  );

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Thermal Analytics</h1>
          <p className="page-desc">Historical temperature analysis from completed inspections — real data only.</p>
        </div>
      </div>

      <div className="toolbar">
        <select aria-label="Device" value={deviceId} onChange={(e) => setDeviceId(Number(e.target.value))} style={{ width: 230 }}>
          <option value={0}>All devices (aggregate)</option>
          {devices.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
        <input type="date" aria-label="From date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} style={{ width: 160 }} />
        <input type="date" aria-label="To date" value={toDate} onChange={(e) => setToDate(e.target.value)} style={{ width: 160 }} />
        <label className="flex" style={{ margin: 0, gap: 8 }}>
          <input type="checkbox" checked={excludeDemo} onChange={(e) => setExcludeDemo(e.target.checked)} />
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>Exclude DEMO readings</span>
        </label>
      </div>

      {error && <ErrorState message={error} />}

      {!deviceId ? (
        <>
          <div className="info-box">
            Selecting a device shows its real thermal history, temperature trend and ΔT trend. The aggregate view below uses inspection statistics.
          </div>
          <ChartCard title="Inspections per day · last 90 days" loading={loading} empty={!loading && daily.length === 0} emptyTitle="No inspections recorded" height={250}>
            <BarsChart data={daily.map((d) => ({ label: d.date.slice(5), count: d.count }))} dataKey="count" color="var(--accent)" />
          </ChartCard>
        </>
      ) : (
        <>
          {simExcluded > 0 && <div className="warn-box">{simExcluded} DEMO / simulated reading(s) excluded from these charts by default.</div>}
          <div className="flex mb-2">
            <Badge tone="tone-blue">Device</Badge>
            <span>{devices.find((d) => d.id === deviceId)?.name ?? `#${deviceId}`}</span>
            <Badge tone={trend?.insufficient ? "plain" : "tone-amber"}>{trend?.insufficient ? "Trend: insufficient data" : `Temp trend: ${trend?.status ?? "—"}`}</Badge>
            {trend?.insufficient ? <span className="muted" style={{ fontSize: 12 }}>{trend.message}</span> : null}
          </div>

          <Card title="Temperature over time" sub="Max temperature per completed inspection (hover for details)">
            {loading ? (
              <LoadingState label="Loading thermal history…" />
            ) : (
              <ThermalLineChart
                data={chartData}
                series={[
                  { key: "temperature", name: "Max temp", color: "var(--orange)" },
                  { key: "reference", name: "Reference", color: "var(--cyan)", kind: "area" },
                  { key: "delta", name: "ΔT", color: "var(--amber)" },
                ]}
                height={280}
                empty={!loading && chartData.length === 0}
                emptyTitle="No thermal history in range"
                emptyBody="No real thermal readings for this device in the selected period."
              />
            )}
          </Card>

          <div className="grid-2">
            <ChartCard title="Temperature delta trend" sub="ΔT (switch vs reference) over inspections" loading={loading} empty={!loading && deltaData.length === 0} emptyTitle="No ΔT history" height={240}>
              <ThermalLineChart data={deltaData} series={[{ key: "delta", name: "ΔT", color: "var(--orange)" }]} height={240} />
            </ChartCard>
            <Card title="Latest readings">
              {chartData.length ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Max</th>
                        <th>Reference</th>
                        <th>ΔT</th>
                        <th>Inspection</th>
                      </tr>
                    </thead>
                    <tbody>
                      {chartData.slice(-8).reverse().map((r, i) => (
                        <tr key={i}>
                          <td>{fmtDate(String(r.timestamp))}</td>
                          <td className="num">{fmtTemp(typeof r.temperature === "number" ? r.temperature : null)}</td>
                          <td className="num">{fmtTemp(typeof r.reference === "number" ? r.reference : null)}</td>
                          <td className="num">{typeof r.delta === "number" ? `${r.delta >= 0 ? "+" : ""}${Number(r.delta).toFixed(1)}°` : "—"}</td>
                          <td className="mono">#{r.inspection_id ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState title="No readings" body="No thermal readings for this device in range." />
              )}
            </Card>
          </div>
        </>
      )}

      <p className="disclaimer">Charts use recorded inspection temperatures only. DEMO / simulated readings are labelled and excluded by default.</p>
    </div>
  );
}
