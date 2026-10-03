import json
import streamlit as st
from snowflake_conn import get_session
from agent_stub import _query_cortex_analyst
import sis_llm

st.set_page_config(page_title="Data Analyst", layout="wide")
st.title("Cortex Analyst Playground")
st.markdown("Interact directly with the semantic model using natural language.")

session = get_session()

if "analyst_messages" not in st.session_state:
    st.session_state["analyst_messages"] = []


@st.cache_data
def analyze_semantic_model():
    prompt = """You are an expert data analyst. A Snowflake database has these tables:
- IT_OT_CONVERGED: TIMESTAMP, EQUIPMENT_ID, TEMPERATURE_C, VIBRATION_RMS, BATCH_ID, SKU_ID
- ASSET_RUL_PREDICTIONS: EQUIPMENT_ID, PREDICTED_FAILURE_TIMESTAMP, RUL_HOURS, PREDICTIVE_CAUSE

1. Write a 3-5 sentence overview of what this data tracks.
2. Generate exactly 3 analytical questions answerable from these tables.

Return JSON: {"overview": "...", "questions": ["Q1?", "Q2?", "Q3?"]}"""
    try:
        return sis_llm.ask_json(session, prompt)
    except Exception:
        return {
            "overview": "A unified dataset mapping high-frequency machine telemetry to ERP batch schedules for equipment degradation tracking.",
            "questions": [
                "What is the average temperature for LINE-2-PACKAGING when producing SKU-899?",
                "Which SKUs cause the highest vibration readings?",
                "Show the predicted failure timestamp for equipment with the lowest RUL.",
            ],
        }


with st.spinner("Analyzing semantic model..."):
    analysis = analyze_semantic_model()

with st.expander("Dataset Overview", expanded=False):
    st.markdown(analysis["overview"])

st.markdown("### Suggested Questions")
for i, suggestion in enumerate(analysis["questions"]):
    if st.button(suggestion, use_container_width=True, key=f"sugg_btn_{i}"):
        st.session_state["analyst_messages"].append({"role": "user", "content": suggestion})
        with st.spinner("Cortex Analyst working..."):
            answer = _query_cortex_analyst(suggestion, context={})
        st.session_state["analyst_messages"].append({"role": "assistant", "content": answer})
        st.experimental_rerun()

st.markdown("---")

# Display conversation history
for idx, msg in enumerate(st.session_state["analyst_messages"]):
    role_label = "**You:**" if msg["role"] == "user" else "**Analyst:**"
    st.markdown(f"{role_label} {msg['content']}")

# Chat input via text_input
prompt = st.text_input("Ask Cortex Analyst about your data...", key="analyst_input")
if prompt:
    st.session_state["analyst_messages"].append({"role": "user", "content": prompt})
    with st.spinner("Cortex Analyst working..."):
        answer = _query_cortex_analyst(prompt, context={})
    st.session_state["analyst_messages"].append({"role": "assistant", "content": answer})
    st.experimental_rerun()
