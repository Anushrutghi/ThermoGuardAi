import { useMemo } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, EmptyState, LoadingState } from "./ui";
import { cls, fmtDate } from "./ui";

const GRID = "var(--border)";
const TICK = { fill: "var(--muted)", fontSize: 11 };

function ChartBody({ loading, empty, emptyTitle, emptyBody, height, children }: { loading?: boolean; empty?: boolean; emptyTitle?: string; emptyBody?: string; height: number; children: React.ReactNode }) {
  if (loading) return <div style={{ height }}><LoadingState label="Loading chart data…" /></div>;
  if (empty) return <div style={{ height }}><EmptyState title={emptyTitle ?? "No data available"} body={emptyBody ?? "There is no recorded data for this view yet."} /></div>;
  return <div style={{ height, width: "100%" }}>{children}</div>;
}

export function ChartCard({
  title,
  sub,
  loading,
  empty,
  emptyTitle,
  emptyBody,
  height = 260,
  legendNote,
  children,
}: {
  title: string;
  sub?: string;
  loading?: boolean;
  empty?: boolean;
  emptyTitle?: string;
  emptyBody?: string;
  height?: number;
  legendNote?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="chart-card" title={title} sub={sub}>
      <ChartBody loading={loading} empty={empty} emptyTitle={emptyTitle} emptyBody={emptyBody} height={height}>
        {children}
      </ChartBody>
      {legendNote && <div className="chart-legend-note">{legendNote}</div>}
    </Card>
  );
}

/** Rich tooltip for thermal time-series (timestamp, temps, delta, inspection). */
function ThermalTooltip({ active, payload, label }: { active?: boolean; payload?: { name?: string; value?: unknown; payload?: Record<string, unknown> }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as Record<string, unknown>;
  const rows: [string, string][] = [];
  if (label) rows.push(["Time", String(label)]);
  if (row.device_name) rows.push(["Device", String(row.device_name)]);
  if (typeof row.temperature === "number") rows.push(["Temperature", `${Number(row.temperature).toFixed(1)} °C`]);
  if (typeof row.reference === "number") rows.push(["Reference", `${Number(row.reference).toFixed(1)} °C`]);
  if (typeof row.delta === "number") rows.push(["Delta", `${Number(row.delta) >= 0 ? "+" : ""}${Number(row.delta).toFixed(1)} °C`]);
  if (row.inspection_id) rows.push(["Inspection", String(row.inspection_id)]);
  if (row.simulated) rows.push(["Source", "DEMO / simulated"]);
  return (
    <div style={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8, padding: "8px 11px", fontSize: 12 }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "flex", gap: 12 }}>
          <span style={{ color: "var(--muted)" }}>{k}</span>
          <span style={{ fontWeight: 650, color: "var(--text)" }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

export function ThermalLineChart({
  data,
  xKey = "timestamp",
  series,
  height = 260,
  loading,
  empty,
  emptyTitle,
  emptyBody,
}: {
  data: Record<string, unknown>[];
  xKey?: string;
  series: { key: string; name: string; color: string; kind?: "line" | "area" }[];
  height?: number;
  loading?: boolean;
  empty?: boolean;
  emptyTitle?: string;
  emptyBody?: string;
}) {
  const memo = useMemo(() => data, [data]);
  return (
    <ChartBody loading={loading} empty={empty || memo.length === 0} emptyTitle={emptyTitle} emptyBody={emptyBody} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={memo} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={TICK} tickFormatter={(v: string) => fmtDate(v, false)} minTickGap={40} />
          <YAxis tick={TICK} width={44} tickFormatter={(v: number) => `${v}°`} />
          <Tooltip content={<ThermalTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s) =>
            s.kind === "area" ? (
              <Area key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} fill={s.color} fillOpacity={0.12} strokeWidth={2} dot={false} />
            ) : (
              <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            )
          )}
        </LineChart>
      </ResponsiveContainer>
    </ChartBody>
  );
}

export function DeltaTrendChart({
  data,
  xKey = "timestamp",
  height = 240,
  loading,
  empty,
}: {
  data: Record<string, unknown>[];
  xKey?: string;
  height?: number;
  loading?: boolean;
  empty?: boolean;
}) {
  return (
    <ChartBody loading={loading} empty={empty || data.length === 0} emptyTitle="No delta history" emptyBody="ΔT (switch vs reference) appears after completed thermal scans." height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={TICK} tickFormatter={(v: string) => fmtDate(v, false)} minTickGap={40} />
          <YAxis tick={TICK} width={44} tickFormatter={(v: number) => `${v}°`} />
          <Tooltip content={<ThermalTooltip />} />
          <Line type="monotone" dataKey="delta" name="ΔT" stroke="var(--orange)" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartBody>
  );
}

export function BarsChart({
  data,
  dataKey,
  color = "var(--accent)",
  xKey = "label",
  height = 240,
  loading,
  empty,
  emptyTitle = "No data",
}: {
  data: Record<string, unknown>[];
  dataKey: string;
  color?: string;
  xKey?: string;
  height?: number;
  loading?: boolean;
  empty?: boolean;
  emptyTitle?: string;
}) {
  return (
    <ChartBody loading={loading} empty={empty || data.length === 0} emptyTitle={emptyTitle} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={TICK} />
          <YAxis tick={TICK} width={40} allowDecimals={false} />
          <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8, fontSize: 12 }} />
          <Bar dataKey={dataKey} fill={color} radius={[4, 4, 0, 0]} maxBarSize={38} />
        </BarChart>
      </ResponsiveContainer>
    </ChartBody>
  );
}

export function RiskDonut({
  data,
  height = 220,
  loading,
  empty,
  onSlice,
}: {
  data: { name: string; value: number; color: string }[];
  height?: number;
  loading?: boolean;
  empty?: boolean;
  onSlice?: (name: string) => void;
}) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return (
    <ChartBody loading={loading} empty={empty || total === 0} emptyTitle="No risk distribution" emptyBody="Risk levels appear once devices have inspection history." height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius={55} outerRadius={82} paddingAngle={2} strokeWidth={0} onClick={(e) => onSlice?.(String((e as { name?: unknown }).name ?? ""))} style={{ cursor: onSlice ? "pointer" : "default" }}>
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8, fontSize: 12 }} formatter={(v, n) => [`${v} device${Number(v) === 1 ? "" : "s"}`, n]} />
        </PieChart>
      </ResponsiveContainer>
    </ChartBody>
  );
}

export { cls };
