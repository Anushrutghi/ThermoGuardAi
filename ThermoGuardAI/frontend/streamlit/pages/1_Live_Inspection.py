"""Live Inspection page — real-time camera feed with AI overlay, plus file/phone modes."""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from frontend.streamlit.api_client import ApiClient, get_api_base  # noqa: E402
from frontend.streamlit.inspection_ui import render_inspection_results  # noqa: E402

st.set_page_config(page_title="Live Inspection", page_icon="🎥", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in from the Dashboard page first.")
    st.stop()

api: ApiClient = st.session_state.api

st.title("🎥 Live Inspection")
st.caption("Real-time pipeline: capture → detection → thermal mapping → fault detection → risk analysis")

# ------------------------------------------------------------------
mode = st.sidebar.radio(
    "Inspection mode",
    ["Quick Scan", "Continuous Monitoring", "Manual Inspection", "Emergency Inspection"],
    index=0,
)
MODE_MAP = {
    "Quick Scan": "quick",
    "Continuous Monitoring": "continuous",
    "Manual Inspection": "manual",
    "Emergency Inspection": "emergency",
}
panels = api.list_panels()
panel_choice = st.sidebar.selectbox("Panel (optional)", ["— None —"] + [f"{p['code']} · {p['name']}" for p in panels])
panel_id = None
if panel_choice != "— None —":
    panel_id = panels[[p["code"] + " · " + p["name"] for p in panels].index(panel_choice)]["id"]

camera_kind = st.sidebar.selectbox(
    "Camera source",
    ["Webcam", "Phone (browser)", "Uploaded image/video", "IP / RTSP"],
)
st.sidebar.caption("Phone mode: open the dashboard on your phone — frames stream over WebSocket.")

ip_url = None
if camera_kind == "IP / RTSP":
    ip_url = st.sidebar.text_input("IP / RTSP URL", placeholder="rtsp://user:pass@192.168.1.50:554/stream", value="")
    if not ip_url:
        st.sidebar.warning("Enter an RTSP/MJPEG URL to enable the IP camera.")


def _start() -> int:
    inspection = api.start_inspection(
        mode=MODE_MAP[mode],
        panel_id=panel_id,
        camera_source=camera_kind.lower().replace(" ", "_"),
    )
    st.session_state.inspection_id = inspection["id"]
    return inspection["id"]


def _finish_session(notes: str = "Stopped from dashboard") -> None:
    """End the session, auto-compile everything into a PDF report, show results."""
    inspection_id = st.session_state.get("inspection_id")
    if inspection_id:
        try:
            api.stop_inspection(inspection_id, notes=notes)
            with st.spinner("Compiling session report (PDF)…"):
                report = api.generate_report(inspection_id)
            st.session_state["generated_report_id"] = report["id"]
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not compile the session report: {exc}")
        st.session_state["completed_inspection_id"] = inspection_id
    st.session_state["inspection_id"] = None
    st.session_state["live_mode"] = False
    st.session_state.pop("snapshot", None)
    st.rerun()


def _stream_url(inspection_id: int | None = None, annotate: bool = True, thermal: bool = False) -> str:
    """Live MJPEG stream URL (browser <img> tags render multipart feeds natively)."""
    token = st.session_state.token
    params = f"token={token}&annotate={'true' if annotate else 'false'}&thermal={'true' if thermal else 'false'}&max_fps=15"
    if inspection_id:
        params += f"&inspection_id={inspection_id}"
    return f"{api.base}/api/v1/cameras/stream?{params}"


def _snapshot(annotate: bool = True, thermal: bool = False) -> bytes | None:
    """Grab the latest annotated frame as a JPEG from the single-frame endpoint."""
    import httpx

    token = st.session_state.token
    url = (
        f"{api.base}/api/v1/cameras/frame?token={token}"
        f"&annotate={'true' if annotate else 'false'}&thermal={'true' if thermal else 'false'}"
    )
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    return resp.content


col_live, col_right = st.columns([2, 1])

with col_live:
    st.subheader("Live feed")

    if camera_kind == "Uploaded image/video":
        uploaded = st.file_uploader("Upload an image (JPG/PNG)", type=["jpg", "jpeg", "png"])
        if uploaded and st.button("Run pipeline on image", type="primary", width="stretch"):
            inspection_id = _start()
            with st.spinner("Running detection pipeline…"):
                api.upload_frame(inspection_id, uploaded.getvalue(), uploaded.name)
            _finish_session(notes="Single image inspection")
        elif uploaded:
            st.image(uploaded, caption="Uploaded image (preview)", width="stretch")

    elif camera_kind == "Phone (browser)":
        st.info(f"📱 Open **{get_api_base()}/mobile** on your phone (use your computer's LAN IP instead of localhost), sign in, and start streaming. Frames arrive here automatically.")
        if st.button("Start continuous session", type="primary", width="stretch"):
            _start()
            st.session_state["phone_mode"] = True
            st.success("Session ready — waiting for phone frames…")

    else:
        # ---------- real-time local camera (webcam / IP) ----------
        live_on = st.session_state.get("live_mode")
        c1, c2, c3 = st.columns(3)
        if c1.button("▶ Start session", type="primary", width="stretch"):
            try:
                if camera_kind == "IP / RTSP":
                    if not ip_url:
                        st.error("Enter an IP/RTSP URL first.")
                        st.stop()
                    api.switch_camera("ip", source=ip_url)
                else:
                    api.switch_camera("webcam")
                _start()
                st.session_state["live_mode"] = True
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Camera start failed: {exc}")
        if c2.button("📸 Snapshot frame", width="stretch", disabled=not live_on):
            try:
                jpeg = _snapshot()
                if jpeg:
                    st.session_state["snapshot"] = jpeg
            except Exception as exc:  # noqa: BLE001
                st.error(f"Snapshot failed: {exc}")
        if c3.button("⏹ End session & generate report", type="primary", width="stretch", disabled=not live_on):
            _finish_session(notes="Stopped from dashboard")

        with st.expander("⚙️ Live view options", expanded=live_on):
            annotate = st.checkbox("AI overlay (detections + faults)", value=True)

        if live_on and st.session_state.get("inspection_id"):
            v1, v2 = st.columns(2)
            with v1:
                st.markdown("##### 📷 Real camera — AI annotated")
                st.markdown(
                    f'<img src="{_stream_url(st.session_state.inspection_id, annotate, thermal=False)}" '
                    f'style="width:100%; border-radius:8px; border:1px solid #333;" alt="real camera feed">',
                    unsafe_allow_html=True,
                )
                st.caption("Detections, temperatures and OVERLOAD warnings update live. Frames are persisted to this session.")
            with v2:
                st.markdown("##### 🌡 Thermal view — heat map")
                st.markdown(
                    f'<img src="{_stream_url(None, annotate, thermal=True)}" '
                    f'style="width:100%; border-radius:8px; border:1px solid #333;" alt="thermal feed">',
                    unsafe_allow_html=True,
                )
                st.caption("🌡️ **DEMO / SIMULATED** thermal view — no real thermal sensor is connected; the heat map is simulated and is **not** a real temperature measurement. Red = simulated extreme heat.")
        elif not live_on:
            st.info("Press **▶ Start session** to open a real-time AI-annotated camera feed.")

    if st.session_state.get("snapshot"):
        st.markdown("#### Snapshot")
        st.image(st.session_state["snapshot"], caption="Latest frame (annotated)", width="stretch")

    completed_id = st.session_state.get("completed_inspection_id")
    if completed_id:
        st.divider()
        try:
            detail = api.get_inspection(completed_id)
            render_inspection_results(detail, api)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load inspection results: {exc}")
        if st.button("✖ Close results", width="stretch"):
            st.session_state.pop("completed_inspection_id", None)
            st.rerun()

with col_right:
    st.subheader("Session controls")
    if st.session_state.get("inspection_id"):
        st.metric("Active inspection", st.session_state.inspection_id)
        if not st.session_state.get("live_mode"):
            if st.button("End session & generate report", type="primary", width="stretch"):
                _finish_session(notes="Stopped from dashboard")
    st.divider()
    st.caption("Thermal mode is active automatically when a thermal source is available (simulator enabled by default).")
