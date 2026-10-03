import streamlit as st
from snowflake_conn import get_session

st.set_page_config(page_title="Alerts History", layout="wide")
st.title("Alerts History & Audit Log")

session = get_session()

st.markdown(
    "This page shows the history of all automated mitigation actions "
    "taken by the Command Center."
)

try:
    history_df = session.sql(
        "SELECT * FROM ALERTS_HISTORY ORDER BY TIMESTAMP DESC"
    ).to_pandas()

    if history_df.empty:
        st.info("No alerts have been mitigated yet.")
    else:
        st.dataframe(history_df, use_container_width=True)

        csv = history_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Alerts History as CSV",
            data=csv,
            file_name="alerts_history.csv",
            mime="text/csv",
        )

except Exception as e:
    err_lower = str(e).lower()
    if "does not exist" in err_lower or "not found" in err_lower:
        st.info("No alerts have been mitigated yet (ALERTS_HISTORY table not found).")
    else:
        st.warning(f"Failed to load alerts history: {e}")
