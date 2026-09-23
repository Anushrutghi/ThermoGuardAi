"""ThermoGuard AI — Streamlit dashboard entry point."""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.core.config import get_settings  # noqa: E402
from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="ThermoGuard AI", page_icon="⚡", layout="wide")


def _init_state() -> None:
    st.session_state.setdefault("token", None)
    st.session_state.setdefault("user", None)
    st.session_state.setdefault("api", ApiClient())
    st.session_state.setdefault("inspection_id", None)


def _do_login(username: str, password: str) -> None:
    try:
        resp = st.session_state.api.login(username.strip(), password.strip())
        st.session_state.token = resp["access_token"]
        st.session_state.user = resp["user"]
        st.session_state.api._token = resp["access_token"]
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Login failed: {exc}")


def _login_page() -> None:
    st.markdown(
        """
        <style>
        .login-hero {text-align:center; padding:3rem 0 1rem;}
        .login-hero h1 {font-size:3rem; color:#0E4DA4; margin-bottom:0.2rem;}
        .login-hero p {color:#555;}
        </style>
        <div class="login-hero">
            <h1>⚡ ThermoGuard AI</h1>
            <p>Real-time electrical panel inspection · fault detection · predictive maintenance</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            _do_login(username, password)

    if get_settings().app_env.lower() != "production":
        st.caption("Dev login — username: `admin` · password: `Admin123!`")
        if st.button("Sign in as dev admin", type="secondary", width="content"):
            _do_login("admin", "Admin123!")

    with st.expander("Production (Firebase) sign-in"):
        st.caption(
            "Production uses Firebase Authentication. Paste a Firebase ID token "
            "(obtained from a Firebase Auth sign-in) to exchange it for the "
            "application session — the local username/password flow is disabled "
            "when AUTH_BACKEND=firebase."
        )
        id_token = st.text_area("Firebase ID token", height=90)
        if st.button("Exchange token", type="secondary"):
            if not id_token.strip():
                st.error("Paste a Firebase ID token first.")
            else:
                try:
                    resp = st.session_state.api.firebase_login(id_token.strip())
                    st.session_state.token = resp["access_token"]
                    st.session_state.user = resp["user"]
                    st.session_state.api._token = resp["access_token"]
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Token exchange failed: {exc}")

    try:
        health = ApiClient().health()
        st.caption(f"Backend status: **{health['status']}** · detector: `{health['detector']}` · thermal: `{health['thermal']}`")
    except Exception:  # noqa: BLE001
        st.warning("Backend unreachable. Start it with `make api`.")


def _sidebar() -> None:
    user = st.session_state.user or {}
    with st.sidebar:
        st.markdown("## ⚡ ThermoGuard AI")
        st.caption(f"Signed in as **{user.get('username', '?')}** ({user.get('role', '?')})")
        if st.session_state.inspection_id:
            st.info(f"Active inspection: **{st.session_state.inspection_id}**")
        if st.button("Sign out", width="stretch"):
            st.session_state.token = None
            st.session_state.user = None
            st.rerun()
        st.divider()
        st.caption("© ThermoGuard AI · v0.1")


def main() -> None:
    _init_state()
    if not st.session_state.token:
        _login_page()
        return
    _sidebar()
    st.title("Dashboard")

    try:
        api = st.session_state.api
        stats = api.inspection_stats()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total inspections", stats["total"])
        c2.metric("Today", stats["today"])
        c3.metric("Open alarms", stats["open_alarms"])
        c4.metric("Avg risk (30d)", f"{stats['avg_risk']:.0f}%")
        sev = stats["by_severity"]
        st.caption(
            f"Severity (30d) — 🟢 healthy: {sev.get('healthy', 0)} · 🟡 warning: {sev.get('warning', 0)} · "
            f"🟠 high: {sev.get('high', 0)} · 🔴 critical: {sev.get('critical', 0)}"
        )

        summary = api.analytics_summary(30)
        st.markdown("### Recent analytics")
        c1, c2, c3 = st.columns(3)
        c1.metric("Faults (30d)", summary["total_faults"])
        c2.metric("Critical faults", summary["critical_faults"])
        c3.metric("Top fault", summary["top_fault_types"][0]["fault_type"] if summary["top_fault_types"] else "—")

        st.markdown("### 🚨 Active alarms")
        try:
            open_alarms = api.list_alarms(open_only=True)
        except Exception:  # noqa: BLE001
            open_alarms = []
        if not open_alarms:
            st.success("No open alarms 🎉")
        else:
            sev_color = {"critical": "#D64545", "high": "#E07B39", "warning": "#E8A33D", "info": "#6B8E9E"}
            for a in open_alarms[:10]:
                with st.container(border=True):
                    color = sev_color.get(a.get("severity", "info"), "#6B8E9E")
                    st.markdown(
                        f"<span style='color:{color};font-weight:800'>{a.get('severity', '').upper()}</span> — "
                        f"{a.get('message', '')}",
                        unsafe_allow_html=True,
                    )
                    st.caption(f"#{a['id']} · {a.get('source', '')} · {a.get('created_at', '')[:19]}")
                    if st.button("Acknowledge", key=f"dash-ack-{a['id']}"):
                        api.acknowledge_alarm(a["id"])
                        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load dashboard data: {exc}")
        st.info("Use the pages in the sidebar for full functionality.")


main()
