# Real Thermal Hardware — Architecture & Integration (S4)

This document describes how ThermoGuard AI connects to real thermal sensors,
what is and is not claimed about them, and how to integrate a physical device.
**No physical thermal hardware was connected during S4** — the production code
below is hardware-ready but **not** hardware-tested. The application therefore
runs in **DEMO / SIMULATED THERMAL** mode unless a real sensor is detected.

> Honesty contract: the backend is the single authority on REAL vs DEMO. The
> frontend renders exactly what `GET /api/v1/thermal/status` reports and never
> infers the source from client state.

---

## 1. Supported hardware architecture

| Sensor | Driver class | Resolution | Connection | Status |
| --- | --- | --- | --- | --- |
| Melexis MLX90640 | `MLX90640Driver` | 32×24 | I2C (smbus2, addr 0x33) | Auto-detected |
| FLIR Lepton | `LeptonDriver` | 160×120 | SPI / UART (`lepton` lib) | Auto-detected |
| Panasonic AMG8833 | `AMG8833Driver` | 8×8 | I2C (`adafruit_amg88xx`) | Auto-detected |
| Seek Thermal Compact Pro | `SeekThermalDriver` | 320×240 | USB (`seekcamera`) | Auto-detected |
| *None present* | `ThermalSimulator` | 64×64 | simulated | Always available (DEMO) |

Selection logic (`ai/thermal/factory.py`):
1. `THERMAL_MODE` picks one driver explicitly, or `auto` probes all real
   drivers in order (`available` = the optional library imported + device
   initialised successfully).
2. The first **real** driver that reports `available=True` wins.
3. Only if no real sensor is found does the factory fall back to the
   simulator. The simulator is always labelled `simulated=True`; every
   inspection record, thermal history row and report carries the flag.

## 2. Production sensor interface (`ai/thermal/base.py`)

```python
class ThermalSensor(BaseThermalSource):
    name: str            # canonical id: mlx90640 | lepton | amg8833 | seek
    simulated: bool      # False for real sensors

    def connect(self) -> bool          # explicit open; sets lifecycle
    def disconnect(self) -> None       # explicit release
    def is_connected(self) -> bool     # present + initialised
    def get_frame(self) -> ThermalFrame | None      # validated frame
    def get_temperature_map(self) -> np.ndarray | None
    def get_ambient_temperature(self) -> float | None  # only if sensor provides it
    def get_metadata(self) -> SensorMetadata
    emissivity: float | None           # only when the sensor supports it
    lifecycle: ThermalLifecycle        # authoritative state
    data_quality: ThermalDataQuality   # per-frame quality gate
```

`SensorMetadata` carries (only when actually available): manufacturer, model,
serial number, resolution, frame rate, connection type, firmware, temperature
unit, emissivity support/emissivity. Every field defaults to `None` /
"unavailable" — values are never invented.

## 3. Sensor lifecycle (canonical states)

S4 + S5 production model:

```
DISCONNECTED → CONNECTING → CONNECTED → INITIALIZING → READY → STREAMING
           → SCANNING → (…) → DISCONNECTED
Any failure anywhere → ERROR   (retry is explicit, never silent)
RECONNECTING is reported when the driver detects a lost connection and is
attempting to re-establish it.
```

Every driver exposes the full interface contract — `connect()` / `disconnect()`
/ `start()` / `stop()` / `read()` / `get_metadata()` / `is_connected()` —
including when hardware is absent (unavailable drivers fail `connect()`/`start()`
honestly and are never reported READY). A sensor is never exposed as READY
unless it actually passed initialization.

The baseline is recorded separately from inspection measurements: the switch
analyzer collects `THERMAL_BASELINE_FRAMES` frames from the frame corners
(unheated surface reference) **before** any switch-region reading is trusted,
then samples `THERMAL_SCAN_FRAMES` into the time series.

## 3a. Measurement status (S5)

Every thermal reading is classified by the backend:

| Status | Meaning |
| --- | --- |
| `MEASURED` | real sensor, connected, valid data — may be treated as an observation |
| `SIMULATED` | DEMO simulator — always labelled, never presented as real |
| `UNAVAILABLE` | no sensor / not connected / no frame available |
| `INVALID` | sensor present but its last frame failed validation |

Only **MEASURED** data is real sensor data. `GET /api/v1/thermal/status` and
`GET /health` expose this classification; the UI renders it verbatim.

## 4. Frame validation (`validate_thermal_frame`)

Every frame is validated before it can enter analytics (single authoritative
path — `SwitchThermalAnalyzer.add()` validates every frame and never skips it):

- **Dimensions** — must be a 2-D array ≥ 2×2 pixels.
- **Numerical type** — the array must have a numeric dtype (non-numeric arrays
  are INVALID, never coerced).
- **Non-finite values** — any NaN/±Inf flags the frame; a fraction above
  `THERMAL_MAX_INVALID_FRACTION` (5%) makes it INVALID.
- **Range** — finite values outside `[THERMAL_FRAME_MIN_C, THERMAL_FRAME_MAX_C]`
  (`[-40, 300] °C` by default) are INVALID.
- **Missing pixels** — counted as non-finite.

Outcome: `VALID` / `LOW_CONFIDENCE` / `INVALID`.

**Chosen NaN/Infinity policy (S4 final hardening):**

| Condition | Outcome | Effect on analytics |
| --- | --- | --- |
| No usable pixels (all NaN/Inf) | INVALID | fully excluded |
| Any value outside thermographic range | INVALID | fully excluded |
| Non-finite fraction > `THERMAL_MAX_INVALID_FRACTION` | INVALID | fully excluded |
| Sparse non-finite pixels (≤ threshold) | LOW_CONFIDENCE | invalid pixels are **explicitly masked** with the frame's own finite median **before** any statistic is computed |

Masking (option A of the review) guarantees that NaN/±Inf values can never
reach the baseline, min/max/mean, percentiles, hotspot detection, gradients,
trends, anomaly/risk analysis or persisted history — analytics always operate
on finite-only arrays. As a defensive guarantee, if masking ever leaves a
non-finite value, the frame is discarded as INVALID. **INVALID frames never
feed risk analysis** — the switch analyzer discards them (logging the reason)
and keeps waiting. A disconnected sensor surfaces `THERMAL SENSOR DISCONNECTED`
behaviour: frames stop, quality goes INVALID, and the controller reports the
unavailable state honestly. There is **no silent fallback REAL → DEMO** during
an inspection; switching to DEMO requires explicitly configuring it.

## 4a. Emissivity authority (active source wins)

The emissivity recorded on a scan result and persisted into `thermal_history`
comes from the **active thermal source of the inspection** — the source
injected into (or resolved by) the inspection controller — never from the
global singleton. When a session source exists, the global singleton is not
authoritative. If the sensor does not support emissivity, `emissivity = None`
(unavailable) is recorded — never an invented value.

## 5. Emissivity / measurement honesty

- Sensors that support emissivity expose `emissivity` (MLX90640/Lepton/Seek
  default to the thermographic standard 0.95; configurable via
  `THERMAL_EMISSIVITY`).
- The AMG8833 and the simulator record `emissivity = None` (unavailable).
- Distance, ambient and reflected temperature are recorded only where the
  sensor/environment actually provides them.

**Disclaimer** (shown in the UI and returned by the status endpoint): thermal
readings depend on sensor accuracy, emissivity, distance, angle, ambient
conditions, surface material, reflection and the environment. Results indicate
*surface thermal patterns* (thermal concentration / potential abnormal
heating) that may require professional inspection — never a confirmed
electrical fault diagnosis.

## 6. ROI / what the system claims

The S1 switch ROI is connected to the thermal frame: the analyzer compares the
**switch region** against a **surrounding-wall reference region** and reports
the thermal distribution (localized vs extended pattern). The system does
**not** claim the thermal sensor "sees electrical wires inside the wall"; it
reports `"Surface thermal concentration detected near the inspection region"`
and recommends professional inspection.

## 7. Hardware detection behaviour

At startup and on first use the factory probes drivers in order. Detection is
**authoritative and backend-side**: `GET /health` and
`GET /api/v1/thermal/status` report `hardware` as exactly `REAL SENSOR` or
`DEMO / SIMULATED THERMAL`, plus `connected`, `lifecycle`, `quality`,
`measurement` (MEASURED/SIMULATED/UNAVAILABLE/INVALID), `metadata` and
`ambient_c`. The React/Streamlit UIs label DEMO data visibly
(`ThermalSourceBadge` / DEMO banners) whenever `simulated` is true.

### Frontend integration (S5)

The React Live Inspection page now consumes the authoritative
`GET /api/v1/thermal/status` endpoint directly (polled + refreshable) and
renders exactly what the backend reports: `REAL THERMAL SENSOR`,
`DEMO / SIMULATED THERMAL`, `THERMAL SENSOR UNAVAILABLE`, plus
connecting/reconnecting/error states — it no longer relies only on inspection
metadata to determine thermal availability. Hardware limitations are shown,
never hidden. When no sensor is present the UI states `THERMAL SENSOR
UNAVAILABLE` and lists the supported sensors (MLX90640, Lepton, AMG8833,
Seek) or DEMO mode as the fallback.

### Frontend thermal-status limitation (explicit)

Frontend thermal mode indicators currently derive from **inspection/session
thermal metadata** (`inspection.thermal_simulated`, WebSocket status
`thermal_simulated` / `thermal_source`) rather than directly consuming the
`/api/v1/thermal/status` endpoint. The backend `/thermal/status` endpoint is
the **authoritative hardware-status source**. Full frontend synchronization
(consuming `/thermal/status` directly) is planned for a future
hardware-integration phase; until then the frontend labels are derived from
session metadata as described above — this limitation is intentional and
documented rather than hidden.

## 8. Calibration requirements (when hardware is present)

1. **Warm-up**: allow the sensor to stabilise (the baseline stage does this
   per inspection).
2. **Emissivity**: set `THERMAL_EMISSIVITY` to the inspected surface's value
   when the sensor supports it; record it in metadata.
3. **Reference**: the corner-reference baseline establishes the local ambient
   before any switch reading is compared.
4. Do **not** claim laboratory-grade accuracy — readings are surface patterns.

## 9. Integration instructions (adding a new sensor)

1. Implement a driver in `ai/thermal/drivers.py` subclassing `ThermalSensor`.
2. Return `True` from `available` only when the device is genuinely present.
3. Return real values from `get_metadata()` (None when unknown).
4. Register it in the factory's candidate list + `THERMAL_MODE` choices.
5. Add unit tests mirroring `tests/unit/test_thermal_validation.py`.
6. Connect the device, run `GET /api/v1/thermal/status`, and verify
   `hardware == "REAL SENSOR"` before trusting readings.

## 10. Known limitations

- Drivers require optional third-party libraries (`smbus2`, `adafruit*`,
  `lepton`, `seekcamera`) and specific OS permissions (I2C/SPI/V4L2/UVC).
- No driver was exercised against physical hardware in this environment —
  real-device behaviour (frame rates, calibration drift, USB re-enumeration)
  is unverified and must be validated on the target device.
- The simulator is deterministic and reproducible for demos/tests only; it
  never feeds real analytics (excluded by `exclude_demo`).
