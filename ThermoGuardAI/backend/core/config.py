"""Application configuration loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = directory containing backend/
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Central settings object. Values come from .env / environment."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "ThermoGuard AI"
    app_env: str = "development"  # development | production
    debug: bool = True
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    streamlit_port: int = 8501

    # Security
    secret_key: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    access_token_name: str = "access_token"
    # Hosts permitted by TrustedHostMiddleware in production (no ports).
    allowed_hosts: str = "localhost,127.0.0.1"

    # Database
    database_url: str = "sqlite:///./database/thermoguard.db"

    # AI / Detection
    model_path: str = "models/electrical_yolo.pt"
    detector_mode: str = "auto"  # auto | yolo | fallback
    # YOLO inference confidence (used only if a real electrical model is loaded).
    confidence_threshold: float = 0.35
    nms_threshold: float = 0.45
    inference_device: str = "auto"  # auto | cpu | cuda | mps
    # CV fallback similarity gate — separate from the YOLO conf so a stale
    # CONFIDENCE_THRESHOLD env value can never weaken it. Only regions matching
    # >=90% structural similarity are reported as components; everything else is
    # filtered so the system never shows uncertain matches.
    fallback_min_confidence: float = 0.90
    # Never present a generic (non-electrical) YOLO model as an electrical
    # detector. The model identity is verified at load time; this flag is for
    # experiments only — enabling it makes results dishonest.
    allow_generic_model: bool = False

    # Performance / Adaptive engine (60 FPS goal on any hardware)
    target_fps: float = 60.0  # pipeline goal; controller tunes scale/stride to hit it
    min_fps: float = 12.0  # below this the controller aggressively cuts load
    perf_min_scale: float = 0.35  # lowest detection resolution multiplier
    perf_max_scale: float = 1.0  # full resolution
    perf_max_stride: float = 8.0  # analyze at most every Nth frame on weak CPUs
    perf_tune_interval_s: float = 1.0  # re-evaluate scale/stride every N seconds
    capture_target_fps: float = 60.0  # background camera capture rate
    stream_max_width: int = 960  # annotated frames are downscaled to this width for transport
    stream_jpeg_quality: int = 80  # baseline JPEG quality for live stream
    stream_min_jpeg_quality: int = 55  # floor when the device is struggling

    # Thermal
    thermal_mode: str = "auto"  # auto | simulator | mlx90640 | lepton | amg8833 | seek
    thermal_sim_hot_component: bool = True
    thermal_ambient_c: float = 25.0
    # S4 frame validation bounds: finite values must stay inside this
    # thermographic range or the frame is INVALID (never enters analytics).
    thermal_frame_min_c: float = -40.0
    thermal_frame_max_c: float = 300.0
    # Max fraction of missing/NaN pixels before a frame is INVALID.
    thermal_max_invalid_fraction: float = 0.05
    # Sensor emissivity. None = unavailable (never invented); when the sensor
    # supports it, operators may configure the surface emissivity here.
    thermal_emissivity: float | None = None

    # Switch-first inspection (S1): the camera finds a wall switch first, then
    # locks an inspection ROI around it before any thermal analysis begins.
    switch_min_confidence: float = 0.55  # CV switch similarity gate
    switch_stable_frames: int = 8  # consecutive frames the switch must be confirmed
    switch_roi_padding: float = 0.35  # ROI expansion beyond the switch box (fraction)
    switch_size_min: float = 0.08  # switch height as frame-height fraction (too small → closer)
    switch_size_max: float = 0.45  # (too big → farther away)
    switch_center_tolerance: float = 0.30  # max center offset from frame center (normalized)
    switch_min_brightness: float = 45.0  # ROI mean brightness floor (guidance)
    switch_min_sharpness: float = 35.0  # ROI Laplacian variance floor (guidance)
    # Thermal stabilization + time-series sampling for the switch scan.
    thermal_baseline_frames: int = 8  # frames collected before trusting readings
    thermal_scan_frames: int = 15  # frames sampled for the time series
    # Anomaly thresholds: switch vs surrounding-wall temperature difference.
    # Deliberately configurable — there is no universal "dangerous" temperature.
    thermal_elevated_delta_c: float = 10.0  # ELEVATED (potential thermal anomaly)
    thermal_abnormal_delta_c: float = 20.0  # ABNORMAL
    thermal_critical_delta_c: float = 35.0  # CRITICAL — professional inspection advised
    thermal_rapid_rise_c_per_min: float = 6.0  # flags a rapid thermal increase

    # Alarms
    alarm_sound_path: str = "assets/sounds/alarm.wav"
    alarm_email_enabled: bool = False
    alarm_webhook_url: str = ""
    # Confirmed circuit-overheating is only reported after this many consecutive
    # analyzed frames (deep heat verification — never trust a single frame).
    heat_confirm_frames: int = 100
    # A candidate component is only shown after it has matched at >=90%
    # similarity for this many consecutive frames (kills flicker/shadows).
    detection_confirm_frames: int = 5

    # Media / Reports
    media_dir: str = "media"
    report_dir: str = "reports/generated"
    max_upload_mb: int = 25

    # CORS
    cors_origins: str = "http://localhost:8501,http://localhost:5173,http://localhost:3000"

    # Seed admin
    seed_admin_username: str = "admin"
    seed_admin_password: str = "Admin123!"
    seed_admin_email: str = "admin@thermoguard.local"
    seed_organization: str = "ThermoGuard"  # organization assigned to the seeded admin (S2 isolation)

    # ------------------------------------------------------------------
    # S6 — Firebase production integration
    # ------------------------------------------------------------------
    # Backend selection. Defaults preserve the local development/test stack.
    #   AUTH_BACKEND:    local    = bcrypt + custom JWT (development/tests)
    #                    firebase = Firebase Authentication ID-token verification
    #   STORAGE_BACKEND: sqlite   = SQLAlchemy/SQLite (development/tests)
    #                    firestore = Firestore documents + Firebase Storage files
    # Production (APP_ENV=production) requires firebase/firestore (validated
    # in validate_security) and must never touch a local database.
    auth_backend: str = "local"  # local | firebase
    storage_backend: str = "sqlite"  # sqlite | firestore

    # Firebase Admin SDK (required when auth_backend=firebase or
    # storage_backend=firestore). The service-account file path is configured
    # here or via GOOGLE_APPLICATION_CREDENTIALS; the file itself is never
    # committed and its contents are never logged.
    firebase_project_id: str = ""
    firebase_storage_bucket: str = ""
    firebase_service_account_path: str = "secrets/thermoguardai-firebase-adminsdk.json"
    # Verify token revocation status on every authentication (production default).
    firebase_check_revoked: bool = True

    # Firebase Emulator Suite hosts (development/tests only — never production).
    # When set, the Admin SDK is pointed at the local emulators so tests and
    # local development never touch the real project.
    firestore_emulator_host: str = ""  # e.g. localhost:8080
    firebase_auth_emulator_host: str = ""  # e.g. localhost:9099
    firebase_storage_emulator_host: str = ""  # e.g. localhost:9199

    # S2 device analytics — health/risk engines are deterministic and
    # configurable; nothing is a universal hardcoded temperature.
    analytics_trend_min_points: int = 3  # inspections required before a trend is declared
    analytics_trend_decreasing_c: float = -0.5  # slope ≤ this → DECREASING
    analytics_trend_stable_c: float = 0.5  # |slope| < this → STABLE
    analytics_trend_slightly_c: float = 1.5  # slope ≥ this → SLIGHTLY_INCREASING
    analytics_trend_increasing_c: float = 3.0  # slope ≥ this → INCREASING (else RAPIDLY_INCREASING)
    analytics_delta_threshold_c: float = 0.5  # ΔT slope threshold for increasing/decreasing
    health_min_inspections: int = 2  # completed inspections before a health score is issued
    health_weight_thermal: float = 0.30
    health_weight_trend: float = 0.25
    health_weight_anomaly: float = 0.20
    health_weight_repeated: float = 0.15
    health_weight_history: float = 0.10
    risk_rapid_rise_bonus: float = 10.0
    risk_repeat_bonus: float = 12.0

    # ------------------------------------------------------------------
    # Derived paths
    # ------------------------------------------------------------------
    @property
    def root_dir(self) -> Path:
        return ROOT_DIR

    @property
    def media_path(self) -> Path:
        return ROOT_DIR / self.media_dir

    @property
    def report_path(self) -> Path:
        return ROOT_DIR / self.report_dir

    @property
    def model_full_path(self) -> Path:
        return ROOT_DIR / self.model_path

    @property
    def alarm_sound_full_path(self) -> Path:
        return ROOT_DIR / self.alarm_sound_path

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_security(self) -> None:
        """Fail fast on unsafe production configuration (called at startup).

        Development keeps permissive defaults; production refuses to run with a
        weak JWT secret, wildcard CORS with credentials, or empty CORS origins.
        """
        problems: list[str] = []
        if self.app_env == "production":
            if self.secret_key == "change-me-to-a-long-random-string" or len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be a strong random value of >= 32 characters in production")
            origins = self.cors_origin_list
            if "*" in origins:
                problems.append("CORS_ORIGINS must not contain '*' in production (credentials are enabled)")
            if not origins:
                problems.append("CORS_ORIGINS must list explicit allowed origins in production")
            if not self.allowed_hosts.strip():
                problems.append("ALLOWED_HOSTS must list the production hostnames")
            if self.debug:
                problems.append("DEBUG must be false in production")
            # S6: production must use Firebase for identity + persistence and
            # must never fall back to the local SQLite/bcrypt backend.
            if self.auth_backend != "firebase":
                problems.append("AUTH_BACKEND must be 'firebase' in production")
            if self.storage_backend != "firestore":
                problems.append("STORAGE_BACKEND must be 'firestore' in production")
            if not self.firebase_project_id:
                problems.append("FIREBASE_PROJECT_ID must be set in production")
            sa_path = Path(self.firebase_service_account_path) if self.firebase_service_account_path else None
            from os import environ

            if not (sa_path and sa_path.is_file()) and not environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                problems.append(
                    "Firebase service-account credentials are required in production "
                    "(set FIREBASE_SERVICE_ACCOUNT_PATH or GOOGLE_APPLICATION_CREDENTIALS)"
                )
        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))

    @property
    def auth_uses_firebase(self) -> bool:
        """True when identity is provided by Firebase Authentication."""
        return self.auth_backend == "firebase"

    @property
    def storage_uses_firestore(self) -> bool:
        """True when persistence uses Firestore + Firebase Storage."""
        return self.storage_backend == "firestore"

    @property
    def firebase_enabled(self) -> bool:
        """True when any Firebase subsystem is active."""
        return self.auth_uses_firebase or self.storage_uses_firestore

    @property
    def firebase_emulator(self) -> bool:
        """True when pointing at the Firebase Emulator Suite (never production)."""
        return bool(
            self.firestore_emulator_host
            or self.firebase_auth_emulator_host
            or self.firebase_storage_emulator_host
        )

    def ensure_dirs(self) -> None:
        """Create all runtime directories."""
        for d in (
            self.media_path,
            self.media_path / "frames",
            self.media_path / "thermal",
            self.media_path / "clips",
            self.report_path,
            ROOT_DIR / "logs",
            ROOT_DIR / "database",
            ROOT_DIR / "models",
            ROOT_DIR / "datasets" / "generated",
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
