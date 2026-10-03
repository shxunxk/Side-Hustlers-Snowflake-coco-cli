import datetime
import json
import streamlit as st
from snowflake_conn import get_session
from agent_stub import (
    ask_investigative_agent,
    _retrieve_oem_constraints,
    _classify_priority,
    post_slack_alert,
)
import sis_llm
from schemas import OEMValidation, MitigationDecision

st.set_page_config(page_title="Investigate", layout="wide")

session = get_session()
context = st.session_state.get("investigate_context", {})

if not context:
    st.info("Please select an alert from the Dashboard to start investigating.")
    st.stop()

equip_id = context.get("equipment_id", "Unknown")
triggering_sku = context.get("sku", "Unknown")
rul = context.get("rul", "Unknown")

current_context_id = f"{equip_id}_{triggering_sku}"
if st.session_state.get("_investigate_context_id") != current_context_id:
    st.session_state["messages"] = []
    st.session_state["_investigate_context_id"] = current_context_id

if "messages" not in st.session_state:
    st.session_state["messages"] = []

try:
    action, priority = _classify_priority(float(rul))
except (TypeError, ValueError):
    action, priority = "MONITOR_EQUIPMENT", "MEDIUM"

_PRIORITY_COLOUR = {"CRITICAL": "red", "HIGH": "orange", "MEDIUM": "gold"}
badge_colour = _PRIORITY_COLOUR.get(priority, "grey")

st.title("Investigative Agent")
st.subheader(f"Investigating {equip_id} (Triggered by SKU: {triggering_sku})")

try:
    rul_float = float(rul)
    hours = int(rul_float)
    minutes = int(round((rul_float - hours) * 60))
    rul_display = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
except (ValueError, TypeError):
    rul_display = f"{rul} hours"

col_l, col_r = st.columns(2)
with col_l:
    st.markdown(f"**Predicted Failure in:** {rul_display}")
with col_r:
    st.markdown(
        f"**Priority:** <span style='color:{badge_colour};font-weight:bold;'>{priority}</span>"
        f" &nbsp;|&nbsp; **Action:** {action}",
        unsafe_allow_html=True,
    )

st.markdown("---")

# --- Suggested Questions ---
st.markdown("### Suggested Troubleshooting Questions")
try:
    questions = sis_llm.ask_json(
        session,
        f'Generate 3 practical troubleshooting questions for equipment {equip_id} producing {triggering_sku} '
        f'with RUL {rul} hours. Return {{"questions": ["Q1?", "Q2?", "Q3?"]}}',
    ).get("questions", [])
except Exception:
    questions = [
        f"What sensor telemetry is driving the RUL drop for {equip_id}?",
        f"Are there historical thermal stress patterns for {equip_id} with {triggering_sku}?",
        f"What OEM corrective actions apply to {equip_id}?",
    ]

for i, q in enumerate(questions):
    if st.button(q, use_container_width=True, key=f"q_btn_{i}"):
        st.session_state["messages"].append({"role": "user", "content": q})
        with st.spinner("Agent analyzing..."):
            answer = ask_investigative_agent(q, context)
        st.session_state["messages"].append({"role": "assistant", "content": answer})
        st.experimental_rerun()

st.markdown("---")

# Display conversation history
for msg in st.session_state["messages"]:
    role_label = "**You:**" if msg["role"] == "user" else "**Agent:**"
    st.markdown(f"{role_label} {msg['content']}")

# Mitigate Impact - Slack via External Access Integration
st.markdown("---")
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("Mitigate Impact", type="primary"):
        with st.spinner("Execution Agent running..."):
            # Fetch latest telemetry
            current_temp, current_vib = None, None
            try:
                tdf = session.sql(
                    f"SELECT TEMPERATURE_C, VIBRATION_RMS FROM IT_OT_CONVERGED "
                    f"WHERE EQUIPMENT_ID = '{equip_id}' ORDER BY TIMESTAMP DESC LIMIT 1"
                ).to_pandas()
                if not tdf.empty:
                    current_temp = float(tdf["TEMPERATURE_C"].iloc[0])
                    current_vib = float(tdf["VIBRATION_RMS"].iloc[0])
            except Exception:
                pass

            oem_text = _retrieve_oem_constraints(equip_id)

            # Fetch SKU-specific thresholds
            dynamic_temp, dynamic_vib = 90.0, 2.3
            try:
                thr_df = session.sql(
                    f"SELECT MAX_TEMP_LIMIT, MAX_VIBRATION_LIMIT FROM OEM_EQUIPMENT_THRESHOLDS "
                    f"WHERE EQUIPMENT_ID = '{equip_id}' AND SKU_ID = '{triggering_sku}' LIMIT 1"
                ).to_pandas()
                if thr_df.empty:
                    thr_df = session.sql(
                        "SELECT MAX_TEMP_LIMIT, MAX_VIBRATION_LIMIT FROM OEM_EQUIPMENT_THRESHOLDS "
                        "WHERE EQUIPMENT_ID = 'ALL' AND SKU_ID = 'ALL' LIMIT 1"
                    ).to_pandas()
                if not thr_df.empty:
                    dynamic_temp = float(thr_df["MAX_TEMP_LIMIT"].iloc[0])
                    dynamic_vib = float(thr_df["MAX_VIBRATION_LIMIT"].iloc[0])
            except Exception:
                pass

            # Deterministic decision
            breaches = []
            if current_temp and current_temp >= dynamic_temp:
                breaches.append(f"Temp {current_temp:.1f}C >= {dynamic_temp}C")
            if current_vib and current_vib >= dynamic_vib:
                breaches.append(f"Vibe {current_vib:.2f} >= {dynamic_vib}")

            rul_f = float(rul) if str(rul).replace(".", "").isdigit() else 0.0

            alert_msg = (
                "*Mitigation Alert: Equipment Failure Detected*\n\n"
                f"*Equipment ID*: {equip_id}\n"
                f"*Active SKU*: {triggering_sku}\n"
                f"*Predicted RUL*: {rul} hours\n"
                f"*Priority*: {priority}\n"
                f"*Recommended Action*: {action}\n"
                f"*Current Telemetry*: Temp={current_temp} C, Vibration={current_vib} mm/s\n"
                f"*OEM Thresholds*: {dynamic_temp}C / {dynamic_vib} mm/s\n"
                f"*Breaches*: {'; '.join(breaches) if breaches else 'None (RUL-based alert)'}"
            )

            slack_ok = post_slack_alert("#oee-production-alerts", alert_msg)

        # Log to ALERTS_HISTORY regardless
        try:
            safe = lambda s: str(s).replace("'", "''")
            session.sql(f"""
                INSERT INTO ALERTS_HISTORY
                    (EQUIPMENT_ID, SKU_ID, ACTION_TAKEN, PRIORITY, RUL_HOURS, OEM_CONSTRAINTS, STATUS)
                VALUES
                    ('{safe(equip_id)}', '{safe(triggering_sku)}', '{safe(action)}',
                     '{safe(priority)}', {rul_f},
                     '{safe(oem_text)[:500]}', '{"SUCCESS" if slack_ok else "SLACK_FAILED"}')
            """).collect()
        except Exception as e:
            st.warning(f"Could not log to ALERTS_HISTORY: {e}")

        if slack_ok:
            st.success(f"Slack alert posted | Priority: **{priority}** | Action: **{action}**")
        else:
            st.warning("Alert recorded in database but Slack notification failed.")

# Chat input via text_input
st.markdown("---")
prompt = st.text_input("Ask a follow-up question", key="followup_input")
if prompt:
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.spinner("Agent analyzing..."):
        answer = ask_investigative_agent(prompt, context)
    st.session_state["messages"].append({"role": "assistant", "content": answer})
    st.experimental_rerun()
