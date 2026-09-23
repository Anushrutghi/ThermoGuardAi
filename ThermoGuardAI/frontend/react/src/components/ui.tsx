import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { ThermalStatus } from "../api/client";

/* ---------------------------------------------------------------------
   Display helpers
   --------------------------------------------------------------------- */
export function fmtTemp(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)} °C`;
}
export function fmtDelta(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)} °C`;
}
export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return withTime
    ? d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) +
        " · " +
        d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
export function fmtShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v)}%`;
}
export function cls(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Risk level → tone/colour class suffix (S2 levels + legacy LOW/MEDIUM/HIGH). */
export function riskTone(level: string | null | undefined): string {
  switch ((level ?? "").toUpperCase()) {
    case "NORMAL":
    case "LOW":
      return "sev-normal";
    case "ELEVATED":
    case "MEDIUM":
      return "sev-elevated";
    case "ABNORMAL":
    case "HIGH":
      return "sev-abnormal";
    case "CRITICAL":
      return "sev-critical";
    default:
      return "plain";
  }
}
/** Derived operational status → tone. */
export function statusTone(status: string | null | undefined): string {
  switch ((status ?? "").toUpperCase()) {
    case "ACTIVE":
    case "MONITORING":
      return "tone-green";
    case "ATTENTION":
      return "tone-amber";
    case "HIGH_RISK":
      return "tone-orange";
    case "CRITICAL":
      return "tone-red";
    case "MAINTENANCE":
      return "tone-purple";
    case "INACTIVE":
      return "tone-gray";
    default:
      return "plain";
  }
}
/** Thermal classification → tone. */
export function classTone(c: string | null | undefined): string {
  switch ((c ?? "").toUpperCase()) {
    case "NORMAL":
      return "tone-green";
    case "ELEVATED":
      return "tone-amber";
    case "ABNORMAL":
      return "tone-orange";
    case "CRITICAL":
      return "tone-red";
    default:
      return "plain";
  }
}

/* ---------------------------------------------------------------------
   Primitives
   --------------------------------------------------------------------- */
export function Card({ children, className, title, sub, tight }: { children: ReactNode; className?: string; title?: string; sub?: string; tight?: boolean }) {
  return (
    <div className={cls("card", tight && "card-tight", className)}>
      {title && (
        <h3 className="card-title">
          {title}
          {sub && <span className="sub">{sub}</span>}
        </h3>
      )}
      {sub && !title && <p className="card-sub">{sub}</p>}
      {children}
    </div>
  );
}

export function Button({
  children,
  variant = "default",
  size,
  block,
  className,
  busy,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "danger" | "ghost" | "success" | "default"; size?: "small" | "big"; block?: boolean; busy?: boolean }) {
  return (
    <button
      className={cls("btn", variant !== "default" && variant, size === "small" && "small", size === "big" && "big", block && "block", className)}
      aria-busy={busy || undefined}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Badge({ children, tone, dot, className }: { children: ReactNode; tone?: string; dot?: string; className?: string }) {
  return (
    <span className={cls("badge", tone, className)}>
      {dot && <span className="dot" style={{ background: dot }} />}
      {children}
    </span>
  );
}
export function RiskBadge({ level }: { level: string | null | undefined }) {
  return <Badge tone={riskTone(level)}>{level ?? "—"}</Badge>;
}
export function StatusBadge({ status }: { status: string | null | undefined }) {
  return <Badge tone={statusTone(status)}>{status ?? "—"}</Badge>;
}
export function ThermalSourceBadge({ simulated, source }: { simulated?: boolean | null; source?: string | null }) {
  if (simulated) return <Badge tone="tone-amber">DEMO / simulated thermal</Badge>;
  if (source) return <Badge tone="tone-green">Real sensor · {source}</Badge>;
  return <Badge tone="plain">No thermal data</Badge>;
}

/**
 * S5: renders the AUTHORITATIVE backend thermal status from
 * GET /api/v1/thermal/status — never inferred from inspection metadata.
 *
 * States: REAL THERMAL SENSOR · DEMO / SIMULATED THERMAL ·
 * THERMAL SENSOR UNAVAILABLE · connecting · disconnected · error.
 */
export function ThermalStatusBadge({ status }: { status: ThermalStatus | null }) {
  if (!status) return <Badge tone="plain">Thermal status unknown</Badge>;
  const lc = (status.lifecycle || "").toUpperCase();
  if (status.hardware === "REAL SENSOR" && status.connected) {
    const detail = status.source ? ` · ${status.source}` : "";
    if (lc === "ERROR") return <Badge tone="tone-red">THERMAL SENSOR ERROR</Badge>;
    if (lc === "RECONNECTING") return <Badge tone="tone-orange">THERMAL SENSOR RECONNECTING</Badge>;
    if (lc === "CONNECTING" || lc === "INITIALIZING") return <Badge tone="tone-blue">THERMAL SENSOR CONNECTING</Badge>;
    // CONNECTED / READY / STREAMING / SCANNING / BASELINE — an initialized,
    // connected device is a real sensor even before the current scan starts.
    return <Badge tone="tone-green">REAL THERMAL SENSOR{detail}</Badge>;
  }
  if (status.simulated) return <Badge tone="tone-amber">DEMO / SIMULATED THERMAL</Badge>;
  if (lc === "ERROR") return <Badge tone="tone-red">THERMAL SENSOR ERROR</Badge>;
  if (lc === "CONNECTING" || lc === "RECONNECTING" || lc === "INITIALIZING") {
    return <Badge tone="tone-blue">THERMAL SENSOR CONNECTING</Badge>;
  }
  return <Badge tone="plain">THERMAL SENSOR UNAVAILABLE</Badge>;
}

export function MetricCard({ label, value, sub, tone, icon }: { label: string; value: ReactNode; sub?: string; tone?: string; icon?: string }) {
  return (
    <div className={cls("metric-card", tone && `tone-${tone}`)}>
      <div className="m-top">
        <span className="m-label">{label}</span>
        {icon && <span className="m-ico">{icon}</span>}
      </div>
      <div className="m-value">{value}</div>
      {sub && <div className="m-sub">{sub}</div>}
    </div>
  );
}

export function EmptyState({ icon = "◇", title, body, action }: { icon?: string; title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="state">
      <div className="s-ico">{icon}</div>
      <div className="s-title">{title}</div>
      {body && <div className="s-body">{body}</div>}
      {action}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state">
      <div className="skeleton" style={{ width: 120, height: 12 }} />
      <div className="skeleton" style={{ width: 220, height: 12 }} />
      <div className="s-title" style={{ color: "var(--muted)", fontWeight: 400 }}>
        {label}
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state">
      <div className="s-ico">⚠</div>
      <div className="s-title">Something went wrong</div>
      <div className="s-body">{message}</div>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Progress({ value, tone, small, label, sub }: { value: number; tone?: string; small?: boolean; label?: string; sub?: string }) {
  return (
    <div className={cls("flex", small ? "gap-8" : "", "spread")} style={{ width: "100%", gap: 10 }}>
      {label && <span className="muted" style={{ fontSize: 13 }}>{label}</span>}
      <div className={cls("progress-track", small && "small")} style={{ flex: 1 }}>
        <span className={cls("progress-fill", tone)} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
      {sub && <span className="num" style={{ fontSize: 12, color: "var(--muted)" }}>{sub}</span>}
    </div>
  );
}

export function ThermalMetric({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: string; tone?: string }) {
  return (
    <div className={cls("thermal-metric", tone)}>
      <div className="tm-label">{label}</div>
      <div className="tm-value">{value}</div>
      {sub && <div className="tm-sub">{sub}</div>}
    </div>
  );
}

export function EvidencePanel({ title = "Why this result?", items }: { title?: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="evidence-panel">
      <h4>{title}</h4>
      <ul className="evidence-list">
        {items.map((e, i) => (
          <li key={i}>{e}</li>
        ))}
      </ul>
    </div>
  );
}

export function Timeline({ events }: { events: { date: string | null; label: string; detail?: string; tone?: string }[] }) {
  if (!events.length) return <EmptyState title="No events yet" body="Events appear after inspections and detections." />;
  return (
    <div className="timeline">
      {events.map((e, i) => (
        <div key={i} className={cls("tl-item", e.tone === "alert" && "tl-alert", e.tone === "warn" && "tl-warn", e.tone === "ok" && "tl-ok")}>
          <div className="tl-date">{fmtDate(e.date, false)}</div>
          <div className="tl-label">{e.label}</div>
          {e.detail && <div className="tl-detail">{e.detail}</div>}
        </div>
      ))}
    </div>
  );
}

export function Modal({ open, title, onClose, children, footer }: { open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
        {footer && <div className="mt-2" style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>{footer}</div>}
      </div>
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { key: string; label: string; count?: number }[]; active: string; onChange: (k: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={active === t.key} className={cls("tab", active === t.key && "active")} onClick={() => onChange(t.key)}>
          {t.label}
          {t.count !== undefined && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

/* ---------------------------------------------------------------------
   Toast system
   --------------------------------------------------------------------- */
type ToastKind = "success" | "error" | "info";
interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}
const ToastCtx = createContext<(kind: ToastKind, message: string) => void>(() => undefined);
export function useToast() {
  return useContext(ToastCtx);
}
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const push = useCallback((kind: ToastKind, message: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, message }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={cls("toast", t.kind)}>
            <span className="t-ico">{t.kind === "success" ? "✓" : t.kind === "error" ? "✕" : "ℹ"}</span>
            <span>{t.message}</span>
            <button className="t-close" aria-label="Dismiss" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
