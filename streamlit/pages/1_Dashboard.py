import streamlit as st
import pandas as pd
from snowflake_conn import get_session

st.set_page_config(page_title="Dashboard", layout="wide")
st.title("IT/OT Data Grid & Alerts")

session = get_session()

if st.session_state.get("pdf_upload_success"):
    st.success("New OEM manual uploaded and parsed. Predictions updated.")
    st.session_state.pdf_upload_success = False

# --- Predictive Alert Cards ---
st.subheader("Predictive Alerts")
try:
    rul_df = session.sql(
        "SELECT * FROM ASSET_RUL_PREDICTIONS ORDER BY RUL_HOURS ASC"
    ).to_pandas()

    if rul_df.empty:
        st.success("No predicted failures in the next 72 hours.")
    else:
        equip_ids = [str(row["EQUIPMENT_ID"]).strip('"') for _, row in rul_df.iterrows()]
        equip_ids_str = "', '".join(equip_ids)
        try:
            sku_df = session.sql(f"""
                SELECT EQUIPMENT_ID, SKU_ID
                FROM IT_OT_CONVERGED
                WHERE EQUIPMENT_ID IN ('{equip_ids_str}')
                QUALIFY ROW_NUMBER() OVER (PARTITION BY EQUIPMENT_ID ORDER BY TIMESTAMP DESC) = 1
            """).to_pandas()
            latest_sku_map = dict(zip(sku_df["EQUIPMENT_ID"], sku_df["SKU_ID"]))
        except Exception:
            latest_sku_map = {}

        cols = st.columns(len(rul_df))
        for col_idx, (_, row) in enumerate(rul_df.iterrows()):
            with cols[col_idx]:
                equip_id = str(row["EQUIPMENT_ID"]).strip('"')
                rul = row["RUL_HOURS"]
                predictive_cause = row.get("PREDICTIVE_CAUSE", "Unknown Cause")
                triggering_sku = latest_sku_map.get(equip_id, "Unknown")
                if triggering_sku == "NONE":
                    triggering_sku = "Machine Changeover"

                if rul <= 6:
                    color, bg_color = "red", "rgba(255, 0, 0, 0.1)"
                elif rul <= 24:
                    color, bg_color = "orange", "rgba(255, 165, 0, 0.1)"
                else:
                    color, bg_color = "#d4af37", "rgba(255, 215, 0, 0.1)"

                st.markdown(
                    f"""
                    <div style="padding: 15px; border-radius: 10px;
                                border: 2px solid {color}; background-color: {bg_color};">
                        <h4>{equip_id}</h4>
                        <p style="font-size: 24px; font-weight: bold; color: {color}; margin:0;">
                            {rul} Hours to Failure
                        </p>
                        <p style="margin-top: 5px; margin-bottom: 5px;">
                            <strong>Predictive Cause:</strong> {predictive_cause}
                        </p>
                        <p style="margin-top: 5px;">
                            <strong>Active SKU:</strong> {triggering_sku}
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if st.button(f"Investigate {equip_id}", key=f"inv_{equip_id}"):
                    st.session_state["investigate_context"] = {
                        "equipment_id": equip_id,
                        "sku": triggering_sku,
                        "rul": rul,
                        "predictive_cause": predictive_cause,
                    }
                    st.info("Context saved. Navigate to **Investigate** page in the sidebar.")

except Exception as e:
    st.warning("Could not load RUL predictions. Has sql/03-analytics.sql been run?")
    st.write(e)

st.markdown("---")

# --- IT/OT Joined Data Grid ---
def render_data_grid():
    st.subheader("Converged Factory Telemetry (IT + OT)")
    try:
        data_df = session.sql(
            "SELECT * FROM IT_OT_CONVERGED ORDER BY TIMESTAMP DESC LIMIT 200"
        ).to_pandas()
        data_df["SKU_ID"] = data_df["SKU_ID"].replace("NONE", "Machine Changeover")

        with st.form("telemetry_filters"):
            col1, col2 = st.columns(2)
            with col1:
                equipment_filter = st.multiselect("Filter by Equipment", data_df["EQUIPMENT_ID"].unique())
            with col2:
                sku_filter = st.multiselect("Filter by SKU", data_df["SKU_ID"].unique())
            st.form_submit_button("Apply Filters")

        if equipment_filter:
            data_df = data_df[data_df["EQUIPMENT_ID"].isin(equipment_filter)]
        if sku_filter:
            data_df = data_df[data_df["SKU_ID"].isin(sku_filter)]

        st.dataframe(data_df, use_container_width=True)
    except Exception as e:
        st.warning(f"Could not load converged data: {e}")

render_data_grid()
