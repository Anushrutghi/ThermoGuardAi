"""Admin page — panels, alarms, camera control."""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="Admin", page_icon="⚙️", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in first.")
    st.stop()

api: ApiClient = st.session_state.api
user = st.session_state.get("user") or {}

st.title("⚙️ Admin")
if user.get("role") not in ("admin", "technician"):
    st.warning("Viewer role: read-only.")
else:
    t_panels, t_alarms, t_cameras = st.tabs(["Panels", "Alarms", "Cameras"])

    with t_panels:
        st.subheader("Registered panels")
        panels = api.list_panels()
        if panels:
            st.dataframe([{"ID": p["id"], "Code": p["code"], "Name": p["name"], "Location": p.get("location") or "—"} for p in panels], width="stretch", hide_index=True)
        st.subheader("Register new panel")
        with st.form("new_panel"):
            name = st.text_input("Panel name")
            code = st.text_input("Panel code (unique)")
            location = st.text_input("Location (optional)")
            desc = st.text_area("Description (optional)")
            if st.form_submit_button("Create panel", type="primary"):
                try:
                    api.create_panel(name, code, location, desc)
                    st.success(f"Panel {code} created")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed: {exc}")

    with t_alarms:
        st.subheader("Active alarms")
        alarms = api.list_alarms(open_only=True)
        if not alarms:
            st.success("No open alarms 🎉")
        for a in alarms:
            with st.container(border=True):
                st.markdown(f"**{a['severity'].upper()}** — {a['message']}")
                st.caption(f"#{a['id']} · {a['source']} · {a['created_at'][:19]}")
                if st.button("Acknowledge", key=f"ack-{a['id']}"):
                    api.acknowledge_alarm(a["id"])
                    st.rerun()

    with t_cameras:
        st.subheader("Camera sources")
        status = api.camera_status()
        st.caption(f"Active: {status.get('active_id') or 'none'} · measured FPS {status.get('fps', 0)}")
        for cam in status["items"]:
            st.markdown(f"- `{cam['id']}` — {cam['name']} ({cam['kind']})")
        st.subheader("Switch camera")
        kind = st.selectbox("Kind", ["webcam", "phone", "ip", "rpi"])
        source = st.text_input("Source (index or URL for ip/rtsp)", placeholder="0 or rtsp://user:pass@host:554/stream")
        if st.button("Switch camera", type="primary"):
            try:
                api.switch_camera(kind, source or None)
                st.success("Camera switched")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Failed: {exc}")
