# ThermoGuard AI — Backend API Contract

Base URL: `http://localhost:8000/api/v1` · Interactive docs at `/docs`
Auth: `Authorization: Bearer <JWT>` · Login via `POST /auth/login`

## Endpoints

### Auth
- `POST /auth/register` `{username, email, full_name?, password}` → User
- `POST /auth/login` `{username, password}` → `{access_token, token_type, expires_in, user}`
- `GET /auth/me` → User *(protected)*

### Cameras
- `GET /cameras` → `{items: [{id, name, kind, status}], active_id, active_kind, fps}`
- `POST /cameras/switch` `{id, name, kind, source?}` — kinds: webcam|usb|file|video|ip|rtsp|mjpeg|phone|rpi
- `POST /cameras/switch/phone` → activates the mobile browser bridge

### Panels
- `GET /panels` → `[Panel]`
- `POST /panels` `{name, code, location?, description?}` → Panel
- `GET /panels/{id}` → `{..., components: [Component]}`
- `POST /panels/{id}/components` `{label, component_type, x, y, w, h}` → Component

### Inspections
- `POST /inspections` `{mode: quick|continuous|manual|scheduled|emergency, panel_id?, camera_source?, notes?}` → Inspection (status=running). New inspections get a permanent `inspection_code` like `TG-20260807-00015`, plus recorded `software_version` / `model_version`
- `GET /inspections?search=&status=&mode=&panel_id=&camera_source=&archived=&from_date=&to_date=&sort=&order=&limit=&offset=` → `{items: [InspectionSummary], total}` — each row includes `health_score` (0–100), `counts{healthy,warning,high,critical}`, `temperature_stats{max,min,avg}`, `inspector`, `panel_name/code/location`, `duration_s`, `faults_count`
- `GET /inspections/export` (same filters) → CSV file
- `GET /inspections/stats` → `{total, today, open_alarms, avg_risk, by_severity}`
- `GET /inspections/{id}` → Inspection + `detections[]` (with `component_code`) + `faults[]` + `incidents[]` + `alarms[]` + `temperature_series[]` + `report_generated_at`
- `POST /inspections/{id}/archive` / `POST /inspections/{id}/unarchive` → Inspection (soft archive)
- `DELETE /inspections/{id}` *(admin)* → deletes the inspection permanently
- `POST /inspections/{id}/frame` (multipart `file`) → full pipeline JSON result
- `POST /inspections/{id}/stop` `{notes?}` / `POST /inspections/{id}/abort` → Inspection. Stopping an inspection with critical faults automatically creates an **incident report** (`INC-…`) and a **maintenance work order** (`MT-…`) for each unique critical fault

### Maintenance
- `GET /maintenance?status=&priority=` → `{items: [MaintenanceRecord], total, by_status}`
- `POST /maintenance` `{component_label?, component_id?, fault_type?, priority, assigned_to?, deadline?, notes?, cost?, inspection_id?}` → MaintenanceRecord (`MT-00001`). `component_id` is auto-linked from `component_label` when omitted; `cost` tracks repair spend
- `POST /maintenance/{id}/update` `{status?: pending|in_progress|completed, assigned_to?, priority?, deadline?, notes?, cost?}` → MaintenanceRecord. Setting status `completed` stamps `completed_at` (cleared when reopened)
- `DELETE /maintenance/{id}` *(admin)* → deletes the work order

### Assets (lifecycle management)
- `GET /assets/overview` → `{panels: [{id, name, code, location, component_count, active_components, at_risk_components, avg_reliability, open_maintenance, total_repair_cost, last_inspection_at, components[]}], totals: {...}}`
- `GET /assets/panels/{id}` → Panel lifecycle row (components with `reliability_score` 0–100, `rul_hours`, `failure_probability`, `failure_count`, `replacement_count`, `total_repair_cost`, `installed_at`, `life_used_pct`, `retired`, `health`)
- `GET /assets/components/{id}` → full lifecycle: `temperature_series[]`, `temperature_stats`, `failures[]`, `maintenance[]`, `limit_c`, RUL + reliability
- `POST /assets/components/{id}/replace` → records a replacement (bumps `replacement_count`, resets `installed_at`/`last_replaced_at`)
- `POST /assets/components/{id}/update` `{installed_at?, expected_life_years?, retired?, last_replaced_at?}` → updates lifecycle fields
- `POST /assets/components/{id}/retire` / `POST /assets/components/{id}/activate` *(admin)* → retire / reactivate a component

### Alarms
- `GET /alarms?open_only=true` → `[Alarm]`
- `POST /alarms/{id}/acknowledge` → Alarm

### Reports
- `POST /reports/inspections/{id}` `{title?, notes?}` → Report (generates PDF)
- `GET /reports` → `[Report]`
- `GET /reports/{id}/download` → PDF file

### Analytics
- `GET /analytics/summary?days=30` → `{total_inspections, total_faults, critical_faults, avg_risk, top_fault_types, severity_breakdown}`
- `GET /analytics/inspections?days=30` → `{items: [{date, count}]}`
- `GET /analytics/temperatures?days=30` → `{items: [{time, label, temp}]}`
- `GET /analytics/component-health` → `{items: [{label, max_temp, avg_temp, readings, health}]}`
- `GET /analytics/predictive` → `{items: [{label, component_type, trend_c_per_inspection, rul_hours, failure_probability, health, recommendation}]}`

### WebSocket — Live inspection
- `ws://host/api/v1/ws/inspect?token=<JWT>`
- Client → server messages (JSON):
  - `{"type":"start_inspection","mode":"continuous","panel_id":null}`
  - `{"type":"frame","jpeg_base64":"<base64 JPEG>"}`
  - `{"type":"stop_inspection","notes":null}`
  - `{"type":"ping"}`
- Server → client messages (JSON):
  - `{"type":"ready","inspection_id":N}`
  - `{"type":"result","jpeg_base64":"...","data":{...}}` — `data` contains
    `detections[]`, `faults[]`, `thermal_stats`, `risk_score`, `fps`,
    `inference_ms`, `component_count`, `thermal_available`, `worst_severity`
  - `{"type":"alarm","severity":"critical","message":"...","recommendation":"..."}`
  - `{"type":"error","message":"..."}` / `{"type":"pong"}`

### System
- `GET /health` → `{status, app, version, database, detector, thermal}`
- `GET /mobile` → phone camera client HTML page

## Sample objects

```jsonc
// Detection
{ "label": "circuit_breaker", "confidence": 0.92, "bbox": [10, 20, 120, 200],
  "temperature": 68.4, "health": "critical", "frame_index": 3 }

// Fault
{ "fault_type": "overheating", "severity": "critical", "confidence": 0.92,
  "component_label": "circuit_breaker", "temperature": 68.4,
  "message": "...", "recommendation": "..." }

// Severity values: "healthy" | "warning" | "high" | "critical"
```

Roles: `admin` (full), `technician` (inspect/report/ack), `viewer` (read-only).
