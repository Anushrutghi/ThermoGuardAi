"""Maintenance Management — work orders created from critical faults + manual records."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from frontend.streamlit.api_client import ApiClient  # noqa: E402

st.set_page_config(page_title="Maintenance Management", page_icon="🔧", layout="wide")

if not st.session_state.get("token"):
    st.warning("Please sign in first.")
    st.stop()

api: ApiClient = st.session_state.api

PRIORITY_COLORS = {"critical": "#D64545", "high": "#E07B39", "medium": "#E8A33D", "low": "#2E8B57"}
STATUS_LABELS = {"pending": "⏳ Pending", "in_progress": "🔨 In Progress", "completed": "✅ Completed"}


@st.cache_data(ttl=5, show_spinner=False)
def fetch_maintenance(status: str | None = None, priority: str | None = None) -> dict:
    return api.list_maintenance(status=status, priority=priority)


st.title("🔧 Maintenance Management")
st.caption(
    "Every critical fault automatically creates a maintenance work order. "
    "Records can be assigned, prioritised and tracked to completion."
)

try:
    data = fetch_maintenance()
    by_status = data.get("by_status", {})
    s1, s2, s3 = st.columns(3)
    s1.metric("Pending", by_status.get("pending", 0))
    s2.metric("In Progress", by_status.get("in_progress", 0))
    s3.metric("Completed", by_status.get("completed", 0))

    with st.sidebar:
        st.subheader("🔎 Filters")
        status_f = st.selectbox("Status", ["All"] + ["pending", "in_progress", "completed"])
        prio_f = st.selectbox("Priority", ["All"] + ["critical", "high", "medium", "low"])

    records = fetch_maintenance(None if status_f == "All" else status_f, None if prio_f == "All" else prio_f).get("items", [])
    if records:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Work Order": r.get("code"),
                        "Equipment": (r.get("component_label") or "—").replace("_", " ").title(),
                        "Fault Type": (r.get("fault_type") or "—").replace("_", " ").title(),
                        "Priority": r.get("priority", "").upper(),
                        "Assigned To": r.get("assigned_to") or "Unassigned",
                        "Status": STATUS_LABELS.get(r.get("status", ""), r.get("status", "")),
                        "Deadline": r.get("deadline") or "—",
                        "Cost": f"${r['cost']:,.2f}" if r.get("cost") is not None else "—",
                        "Created": (r.get("created_at") or "")[:19],
                    }
                    for r in records
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No maintenance records match the current filters.")

    st.markdown("---")

    t_create, t_update = st.tabs(["➕ New work order", "✏️ Update work order"])

    with t_create:
        with st.form("create_mt", border=False):
            c1, c2 = st.columns(2)
            component = c1.text_input("Equipment / component", placeholder="Breaker B2")
            fault_type = c2.text_input("Fault type", placeholder="overheating")
            c3, c4, c5, c6 = st.columns(4)
            priority = c3.selectbox("Priority", ["critical", "high", "medium", "low"])
            assigned = c4.text_input("Assigned to", placeholder="Technician")
            deadline = c5.date_input("Deadline", value=date.today() + timedelta(days=7))
            cost = c6.number_input("Cost ($)", min_value=0.0, value=0.0, step=10.0)
            notes = st.text_area("Notes", height=70)
            if st.form_submit_button("Create work order", type="primary"):
                api.create_maintenance(
                    component_label=component or None,
                    fault_type=fault_type or None,
                    priority=priority,
                    assigned_to=assigned or None,
                    deadline=deadline.isoformat(),
                    cost=cost if cost > 0 else None,
                    notes=notes or None,
                )
                st.success(f"Work order created for {component or 'panel'}")
                fetch_maintenance.clear()
                st.rerun()

    with t_update:
        if records:
            codes = {r.get("code"): r for r in records}
            with st.form("update_mt", border=False):
                sel = st.selectbox("Work order", list(codes.keys()))
                rec = codes[sel]
                c1, c2 = st.columns(2)
                new_status = c1.selectbox("Status", ["pending", "in_progress", "completed"], index=["pending", "in_progress", "completed"].index(rec.get("status", "pending")))
                new_prio = c2.selectbox("Priority", ["critical", "high", "medium", "low"], index=["critical", "high", "medium", "low"].index(rec.get("priority", "medium")))
                c3, c4, c5 = st.columns(3)
                new_assigned = c3.text_input("Assigned to", value=rec.get("assigned_to") or "")
                new_deadline = c4.date_input("Deadline", value=date.fromisoformat(rec["deadline"]) if rec.get("deadline") else date.today() + timedelta(days=7))
                new_cost = c5.number_input("Cost ($)", min_value=0.0, value=float(rec.get("cost") or 0.0), step=10.0)
                new_notes = st.text_area("Notes", value=rec.get("notes") or "", height=70)
                if st.form_submit_button("Save changes", type="primary"):
                    api.update_maintenance(
                        rec["id"],
                        status=new_status,
                        priority=new_prio,
                        assigned_to=new_assigned or None,
                        deadline=new_deadline.isoformat(),
                        cost=new_cost if new_cost > 0 else None,
                        notes=new_notes or None,
                    )
                    st.success(f"{sel} updated → {STATUS_LABELS.get(new_status, new_status)}")
                    fetch_maintenance.clear()
                    st.rerun()
        else:
            st.info("No records to update — create one or run an inspection that triggers a critical fault.")

except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load maintenance records: {exc}")
