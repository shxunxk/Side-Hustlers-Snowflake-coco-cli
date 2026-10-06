import streamlit as st
from snowflake_conn import get_session

st.set_page_config(
    page_title="OEEE Command Center",
    layout="wide",
    initial_sidebar_state="expanded",
)

session = get_session()

# ── Check if infrastructure is deployed ──────────────────────────────────────
needs_setup = False
try:
    result = session.sql(
        "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_OT_TELEMETRY"
    ).collect()
    if not result or int(result[0][0]) == 0:
        needs_setup = True
except Exception:
    needs_setup = True

if needs_setup:
    st.warning(
        "Infrastructure not yet deployed. Navigate to the **Setup** page in the sidebar "
        "and click **Deploy Infrastructure** to get started."
    )

# ── Header ───────────────────────────────────────────────────────────────────
try:
    ctx = session.sql(
        "SELECT CURRENT_ACCOUNT() AS ACCT, CURRENT_WAREHOUSE() AS WH"
    ).collect()[0]
    acct = ctx["ACCT"]
    wh = ctx["WH"]
except Exception:
    acct = "Unknown"
    wh = "Unknown"

st.title("SKU-Specific OEE Degradation Tracker")

col_s1, col_s2, col_s3 = st.columns(3)
with col_s1:
    st.success(f"**Snowflake Account:** `{acct}`")
with col_s2:
    st.info(f"**Warehouse:** `{wh}`")
with col_s3:
    st.success("**Slack Channel:** `#oee-production-alerts`")

st.markdown("---")

st.markdown(
    """
    Welcome to the **OEE Degradation Command Center**!
    
    The autonomous pipeline continuously fuses **IT work orders** with **OT machine telemetry** 
    in Snowflake, forecasting Remaining Useful Life (RUL) and triggering agentic Slack mitigations.
    """
)

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(
        '<div style="background-color: rgba(56, 189, 248, 0.08); padding: 20px; '
        'border-radius: 10px; border: 1px solid rgba(56, 189, 248, 0.3);">'
        "<h3>Live Dashboard</h3>"
        "<p>Monitor converged IT/OT telemetry and real-time ML-predicted RUL alert cards.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        '<div style="background-color: rgba(168, 85, 247, 0.08); padding: 20px; '
        'border-radius: 10px; border: 1px solid rgba(168, 85, 247, 0.3);">'
        "<h3>Investigate Agent</h3>"
        "<p>Multi-agent investigative assistant with root-cause analysis and Slack mitigation.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
with col3:
    st.markdown(
        '<div style="background-color: rgba(34, 197, 94, 0.08); padding: 20px; '
        'border-radius: 10px; border: 1px solid rgba(34, 197, 94, 0.3);">'
        "<h3>Alerts History</h3>"
        "<p>Permanent audit trail of all automated mitigation actions.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

st.caption("Use the sidebar to navigate between pages.")
