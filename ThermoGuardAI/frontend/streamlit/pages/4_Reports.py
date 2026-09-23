"""Reports page — list and download generated PDFs."""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="Reports", page_icon="📄", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in first.")
    st.stop()

api: ApiClient = st.session_state.api

st.title("📄 Inspection Reports")

try:
    reports = api.list_reports()
    if not reports:
        st.info("No reports generated yet. Generate one from the Inspection History page.")
        st.stop()
    for r in reports:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{r['title']}**")
            c1.caption(f"Inspection #{r['inspection_id']} · {r['generated_at'][:19]} · Risk {r['risk_score']:.0f}/100")
            c2.markdown(f"by {r['generated_by'] or '—'}")
            if c3.button("⬇ Download", key=f"dl-{r['id']}"):
                try:
                    with st.spinner("Preparing PDF…"):
                        st.session_state[f"pdf_{r['id']}"] = api.download_report(r["id"])
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Download failed: {exc}")
            if st.session_state.get(f"pdf_{r['id']}"):
                st.download_button(
                    "💾 Save PDF",
                    data=st.session_state[f"pdf_{r['id']}"],
                    file_name=f"{r['title']}.pdf",
                    mime="application/pdf",
                    key=f"save_{r['id']}",
                )
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load reports: {exc}")
