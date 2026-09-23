// Thin typed API client for the ThermoGuard backend (S1/S2/S3 surface).

export const API_BASE = "/api/v1";

export type Severity = "healthy" | "warning" | "high" | "critical";

export interface User {
  id: number;
  username: string;
  email: string;
  full_name?: string;
  role: string;
  organization?: string | null;
  is_active: boolean;
  created_at?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Detection {
  label: string;
  confidence: number;
  bbox: [number, number, number, number];
  temperature: number | null;
  health: Severity;
  frame_index?: number;
  component_code?: string | null;
}

export interface Fault {
  id?: number;
  fault_type: string;
  severity: Severity;
  confidence: number;
  component_label?: string;
  temperature?: number | null;
  message: string;
  recommendation?: string;
}

export interface Incident {
  id: number;
  code: string;
  inspection_id?: number | null;
  fault_type: string;
  severity: string;
  component_label?: string | null;
  temperature?: number | null;
  occurred_at: string;
  suggested_action?: string | null;
  inspector?: string | null;
  notes?: string | null;
}

export interface Alarm {
  id: number;
  severity: Severity;
  message: string;
  source: string;
  acknowledged: boolean;
  created_at: string;
  media_path?: string;
}

export interface ThermalStats {
  max_temp: number;
  min_temp: number;
  avg_temp: number;
  delta: number;
  hotspot_x: number;
  hotspot_y: number;
  gradient: number;
  heat_spread: number;
}

export interface PerfTelemetry {
  target_fps: number;
  achieved_fps: number;
  scale: number;
  stride: number;
  grade: string;
  ema_inference_ms: number;
  frames_analyzed: number;
  frames_total: number;
}

export interface PipelineResult {
  detections: Detection[];
  faults: Fault[];
  thermal_stats: ThermalStats | null;
  risk_score: number;
  fps: number;
  inference_ms: number;
  component_count: number;
  thermal_available: boolean;
  thermal_source?: string | null;
  thermal_simulated?: boolean;
  worst_severity: Severity;
  analyzed: boolean;
  perf: PerfTelemetry | null;
}

export interface Inspection {
  id: number;
  mode: string;
  status: string;
  started_at: string;
  ended_at?: string;
  frames_processed: number;
  avg_fps: number;
  risk_score: number;
  component_count: number;
  camera_source: string;
  notes?: string;
  inspection_code?: string;
  software_version?: string;
  model_version?: string;
  thermal_source?: string | null;
  thermal_simulated?: boolean;
  archived?: boolean;
  health_score?: number;
  duration_s?: number;
  inspector?: string;
  device_id?: number | null;
  device_name?: string | null;
  device_location?: string | null;
  panel_name?: string;
  panel_code?: string;
  panel_location?: string;
  counts?: { healthy: number; warning: number; high: number; critical: number };
  temperature_stats?: { max_temp: number | null; min_temp: number | null; avg_temp: number | null };
  faults_count?: number;
}

export interface InspectionDetail extends Inspection {
  detections: Detection[];
  faults: Fault[];
  incidents: Incident[];
  alarms: Alarm[];
  temperature_series: { time: string; label: string; temp: number }[];
  report_generated_at?: string | null;
}

export interface Report {
  id: number;
  inspection_id?: number;
  title: string;
  risk_score: number;
  generated_by?: string;
  generated_at: string;
}

export interface Prediction {
  label: string;
  component_type: string;
  trend_c_per_inspection: number;
  rul_hours: number | null;
  failure_probability: number;
  health: Severity;
  recommendation: string;
}

export interface Device {
  id: number;
  name: string;
  location?: string | null;
  building?: string | null;
  floor?: string | null;
  room?: string | null;
  device_type: string;
  manufacturer?: string | null;
  model?: string | null;
  serial_number?: string | null;
  rated_voltage?: number | null;
  rated_current?: number | null;
  installation_date?: string | null;
  last_maintenance_date?: string | null;
  next_inspection_date?: string | null;
  organization?: string | null;
  notes?: string | null;
  status: string;
  risk_level: string;
  derived_status: string;
  status_override: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  inspections_count: number;
  last_inspection_at?: string | null;
  last_risk_score?: number | null;
  last_max_temp?: number | null;
  last_thermal_simulated?: boolean | null;
}

export interface DeviceHistory {
  device_id: number;
  name: string;
  derived_status: string;
  total_inspections: number;
  first_inspection?: string | null;
  latest_inspection?: string | null;
  latest_temperature?: number | null;
  max_temperature?: number | null;
  avg_temperature?: number | null;
  latest_thermal_delta?: number | null;
  latest_anomaly?: string | null;
  anomaly_count: number;
  repeated_anomaly_kinds: number;
  current_risk?: string | null;
  current_health?: number | null;
  maintenance_recommendation?: string | null;
  note?: string | null;
  thermal_history_total?: number;
  thermal_simulated_count?: number;
  simulated_excluded_from_temperatures?: boolean;
}

export interface DeviceDetail extends Device {
  recent_inspections: Inspection[];
  history: DeviceHistory | null;
}

export interface ThermalHistoryItem {
  id: number;
  device_id: number;
  inspection_id: number | null;
  timestamp: string | null;
  max_temp: number | null;
  min_temp: number | null;
  avg_temp: number | null;
  reference_temp: number | null;
  delta: number | null;
  hotspot_x: number | null;
  hotspot_y: number | null;
  heat_path: string | null;
  classification: string | null;
  simulated: boolean;
  sensor: string | null;
}

export interface ThermalHistoryResponse {
  items: ThermalHistoryItem[];
  total: number;
  exclude_demo: boolean;
  simulated_excluded: number;
  note?: string | null;
}

export interface TrendResult {
  status: string;
  slope_c_per_inspection?: number | null;
  slope_c_per_delta?: number | null;
  points: { timestamp: string | null; temperature?: number; delta?: number; simulated: boolean }[];
  insufficient: boolean;
  message: string;
}

export interface HealthContributor {
  name: string;
  rating: string;
  score: number;
  weight: number;
  detail: string;
}

export interface HealthResult {
  score: number | null;
  rating: string | null;
  contributors: HealthContributor[];
  insufficient: boolean;
  message: string;
  disclaimer?: string;
  simulated_excluded?: number;
}

export interface RiskResult {
  level: string | null;
  risk_score: number | null;
  evidence: string[];
  reasoning: string | null;
  insufficient: boolean;
  message: string;
}

export interface TimelineEvent {
  date: string | null;
  type: string;
  label: string;
  detail: string;
}

export interface MaintenanceOpenOrder {
  id: number;
  code: string;
  priority: string;
  status: string;
  fault_type?: string | null;
  created_at?: string | null;
}

export interface DeviceMaintenanceResult {
  recommendation?: string | null;
  basis: string[];
  risk_level?: string | null;
  open_orders: MaintenanceOpenOrder[];
  insufficient: boolean;
}

export interface ComparisonRow {
  inspection_id: number | null;
  timestamp: string | null;
  temperature: number | null;
  thermal_delta: number | null;
  classification: string | null;
  simulated: boolean;
}

export interface ComparisonResult {
  current: ComparisonRow | null;
  previous: ComparisonRow | null;
  temperature_change: number | null;
  delta_change: number | null;
  trend: string | null;
  insufficient: boolean;
  message: string;
}

export interface DeviceAnomalyItem {
  inspection_id: number | null;
  inspection_code?: string | null;
  occurred_at: string | null;
  fault_type: string;
  kind: string;
  severity: string;
  temperature: number | null;
  message: string;
}

export interface RepeatedAnomalyKind {
  kind: string;
  fault_type: string;
  count: number;
  inspections: number[];
}

export interface DeviceAnomaliesResponse {
  items: DeviceAnomalyItem[];
  total: number;
  repeated: boolean;
  repeated_count: number;
  kinds: RepeatedAnomalyKind[];
  total_anomalies: number;
  message: string;
}

export interface AnomalyCenterItem {
  fault_id: number;
  inspection_id: number;
  inspection_code?: string | null;
  device_id: number;
  device_name: string;
  occurred_at: string | null;
  fault_type: string;
  kind: string;
  severity: string;
  temperature: number | null;
  message: string;
  recommendation?: string | null;
  repeated: boolean;
  occurrences: number;
}

export interface DashboardData {
  total_devices: number;
  healthy: number;
  monitoring: number;
  attention: number;
  high_risk: number;
  critical: number;
  maintenance: number;
  inactive: number;
  inspections_this_month: number;
  anomalies_this_month: number;
  devices_requiring_maintenance: number;
  avg_temperature: number | null;
  max_temperature: number | null;
}

export interface MaintenanceListItem {
  id: number;
  code: string;
  inspection_id?: number | null;
  component_label?: string | null;
  fault_type?: string | null;
  priority: string;
  assigned_to?: string | null;
  status: string;
  deadline?: string | null;
  notes?: string | null;
  cost?: number | null;
  completed_at?: string | null;
  created_at: string;
}

export interface MaintenanceList {
  items: MaintenanceListItem[];
  total: number;
  by_status: Record<string, number>;
}

export interface TeamUser {
  id: number;
  username: string;
  email: string;
  full_name?: string | null;
  role: string;
  is_active: boolean;
  organization?: string | null;
  created_at?: string | null;
}

export interface SwitchGuidance {
  messages: string[];
  checks: Record<string, boolean>;
  ready: boolean;
  score: number;
}

export interface ThermalScanComplete {
  thermal_available: boolean;
  switch_temp: number | null;
  wall_temp: number | null;
  ambient: number | null;
  delta_vs_wall: number | null;
  max_temp: number | null;
  hotspot: { x: number; y: number } | null;
  heat_path: string;
  trend_c_per_min: number | null;
  trend_delta_c: number | null;
  rapid_increase: boolean;
  stability_c: number | null;
  classification: string;
  risk_score: number;
  evidence: string[];
  series: { t: number; switch: number; wall: number }[];
  message: string;
}

// S5: authoritative backend thermal status (GET /api/v1/thermal/status).
// The backend is the single source of truth for REAL vs DEMO/SIMULATED —
// the frontend renders exactly this and never infers the source itself.
export interface ThermalStatus {
  mode: string;
  source: string;
  simulated: boolean;
  hardware: "REAL SENSOR" | "DEMO / SIMULATED THERMAL";
  connected: boolean;
  lifecycle: string;
  quality: string;
  measurement: "MEASURED" | "SIMULATED" | "UNAVAILABLE" | "INVALID";
  ambient_c: number | null;
  emissivity: number | null;
  metadata: {
    manufacturer?: string | null;
    model?: string | null;
    serial_number?: string | null;
    resolution?: string | null;
    frame_rate?: number | null;
    connection_type?: string | null;
    firmware?: string | null;
    temperature_unit?: string | null;
    emissivity?: number | null;
    emissivity_supported?: boolean;
    simulated?: boolean;
  };
  disclaimer: string;
}

export interface SwitchStatus {
  state: string;
  state_label: string;
  message: string;
  error?: string | null;
  guidance: SwitchGuidance | null;
  switch_bbox: [number, number, number, number] | null;
  switch_confidence: number;
  stable_frames: number;
  stable_required: number;
  roi: [number, number, number, number] | null;
  thermal_available: boolean;
  thermal_simulated: boolean;
  thermal_source?: string | null;
  thermal_stage?: string | null;
  baseline_frames: number;
  baseline_required: number;
  scan_frames: number;
  scan_required: number;
  scan_sample: { t: number; switch: number; wall: number } | null;
  complete: ThermalScanComplete | null;
  progress: number;
}

let token: string | null = null;
const TOKEN_KEY = "tg_token";
try {
  token = sessionStorage.getItem(TOKEN_KEY);
} catch {
  token = null;
}

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — token stays in memory only */
  }
}

export function getToken() {
  return token;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  // ---- Auth ---------------------------------------------------------
  login: (username: string, password: string) =>
    request<LoginResponse>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  register: (username: string, email: string, password: string, full_name?: string) =>
    request<User>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, email, password, full_name: full_name ?? null }),
    }),
  /** Exchange a verified Firebase ID token for the application session token. */
  firebaseLogin: (idToken: string) =>
    request<LoginResponse>("/auth/firebase", { method: "POST", body: JSON.stringify({ id_token: idToken }) }),

  // ---- Devices ------------------------------------------------------
  listDevices: () => request<Device[]>("/devices"),
  createDevice: (payload: Partial<Device>) => request<Device>("/devices", { method: "POST", body: JSON.stringify(payload) }),
  updateDevice: (id: number, payload: Partial<Device>) =>
    request<Device>(`/devices/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteDevice: (id: number) => request<{ message: string }>(`/devices/${id}`, { method: "DELETE" }),
  getDevice: (id: number) => request<DeviceDetail>(`/devices/${id}`),
  getDeviceHistory: (id: number) => request<DeviceHistory>(`/devices/${id}/history`),
  getDeviceInspections: (id: number, params?: { status?: string; limit?: number; offset?: number }) =>
    request<{ items: Inspection[]; total: number }>(`/devices/${id}/inspections${qs(params ?? {})}`),
  getDeviceThermalHistory: (id: number, params?: { exclude_demo?: boolean; limit?: number; offset?: number }) =>
    request<ThermalHistoryResponse>(`/devices/${id}/thermal-history${qs(params ?? {})}`),
  getDeviceTemperatureTrend: (id: number, excludeDemo = true) =>
    request<TrendResult>(`/devices/${id}/temperature-trend${qs({ exclude_demo: excludeDemo })}`),
  getDeviceDeltaTrend: (id: number, excludeDemo = true) =>
    request<TrendResult>(`/devices/${id}/delta-trend${qs({ exclude_demo: excludeDemo })}`),
  getDeviceAnomalies: (id: number, params?: { limit?: number; offset?: number }) =>
    request<DeviceAnomaliesResponse>(`/devices/${id}/anomalies${qs(params ?? {})}`),
  getDeviceHealth: (id: number, excludeDemo = true) =>
    request<HealthResult>(`/devices/${id}/health${qs({ exclude_demo: excludeDemo })}`),
  getDeviceRisk: (id: number, excludeDemo = true) =>
    request<RiskResult>(`/devices/${id}/risk${qs({ exclude_demo: excludeDemo })}`),
  getDeviceTimeline: (id: number) => request<{ items: TimelineEvent[] }>(`/devices/${id}/timeline`),
  getDeviceMaintenance: (id: number) => request<DeviceMaintenanceResult>(`/devices/${id}/maintenance`),
  getDeviceComparison: (id: number, excludeDemo = true) =>
    request<ComparisonResult>(`/devices/${id}/comparison${qs({ exclude_demo: excludeDemo })}`),

  // ---- Dashboard ----------------------------------------------------
  dashboard: () => request<DashboardData>("/devices/dashboard"),

  // ---- Inspections --------------------------------------------------
  stats: () => request<{ total: number; today: number; open_alarms: number; avg_risk: number; by_severity: Record<string, number> }>("/inspections/stats"),
  listInspections: (params?: {
    search?: string;
    status?: string;
    mode?: string;
    device_id?: number;
    from_date?: string;
    to_date?: string;
    sort?: string;
    order?: string;
    limit?: number;
    offset?: number;
  }) => request<{ items: Inspection[]; total: number }>(`/inspections${qs(params ?? {})}`),
  getInspection: (id: number) => request<InspectionDetail>(`/inspections/${id}`),
  startInspection: (payload: { mode?: string; panel_id?: number | null; device_id?: number | null; camera_source?: string; notes?: string }) =>
    request<Inspection>("/inspections", { method: "POST", body: JSON.stringify(payload) }),
  stopInspection: (id: number, notes?: string) =>
    request<Inspection>(`/inspections/${id}/stop`, { method: "POST", body: JSON.stringify({ notes: notes ?? null }) }),

  // ---- Alarms / anomalies --------------------------------------------
  listAlarms: (openOnly = false) => request<Alarm[]>(`/alarms?open_only=${openOnly}`),
  acknowledgeAlarm: (id: number) => request<Alarm>(`/alarms/${id}/acknowledge`, { method: "POST" }),
  listAnomalies: (params?: {
    severity?: string;
    device_id?: number;
    from_date?: string;
    to_date?: string;
    limit?: number;
    offset?: number;
  }) => request<{ items: AnomalyCenterItem[]; total: number }>(`/anomalies${qs(params ?? {})}`),

  // ---- Maintenance ----------------------------------------------------
  listMaintenance: (params?: { status?: string; priority?: string }) =>
    request<MaintenanceList>(`/maintenance${qs(params ?? {})}`),

  // ---- Reports ---------------------------------------------------------
  listReports: () => request<Report[]>("/reports"),
  generateReport: (inspectionId: number, notes?: string) =>
    request<Report>(`/reports/inspections/${inspectionId}`, { method: "POST", body: JSON.stringify({ notes: notes ?? null }) }),
  downloadReport: async (reportId: number) => {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const resp = await fetch(`${API_BASE}/reports/${reportId}/download`, { headers });
    if (!resp.ok) throw new Error(`Download failed (HTTP ${resp.status})`);
    return resp.blob();
  },

  // ---- Team ------------------------------------------------------------
  listUsers: () => request<{ items: TeamUser[]; total: number }>("/users"),

  // ---- Thermal status (S5) ----------------------------------------------
  thermalStatus: () => request<ThermalStatus>("/thermal/status"),

  // ---- Analytics -------------------------------------------------------
  summary: (days = 30) =>
    request<{ total_inspections: number; total_faults: number; critical_faults: number; avg_risk: number; top_fault_types: { fault_type: string; count: number }[]; severity_breakdown: Record<string, number> }>(`/analytics/summary?days=${days}`),
  inspectionsPerDay: (days = 30) => request<{ items: { date: string; count: number }[] }>(`/analytics/inspections?days=${days}`),
  temperatures: (days = 30) => request<{ items: { time: string; label: string; temp: number }[] }>(`/analytics/temperatures?days=${days}`),
  predictive: () => request<{ items: Prediction[] }>("/analytics/predictive"),
};

export function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss://" : "ws://";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}${window.location.host}${API_BASE}/ws/inspect${query}`;
}
