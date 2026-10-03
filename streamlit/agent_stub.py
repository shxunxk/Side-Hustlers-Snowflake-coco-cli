"""SiS agent stub  Cortex-only LLM, keyword OEM search, Cortex Analyst via SQL."""
import json
import re
import streamlit as st
from snowflake_conn import get_session
import sis_llm

_DB = "OEE_COMMAND_CENTER"
_SCHEMA = "FACTORY_FLOOR"
CHUNK_TABLE = f"{_DB}.{_SCHEMA}.OEM_MANUAL_CHUNKS"

_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into",
    "are", "was", "were", "what", "which", "how", "does", "doc",
    "documents", "document", "retrieve", "return", "contents",
    "operating", "manual", "line", "packaging",
}


def _query_terms(query):
    terms = re.findall(r"[a-z0-9]+", query.lower())
    return [t for t in terms if len(t) > 2 and t not in _STOP_WORDS]


_FALLBACK_OEM_TEXT = (
    "Max Sustained Temp: 90C, Max Vibration: 2.3 mm/s (fallback  OEM chunks unavailable)"
)


def _retrieve_oem_constraints(equipment_id):
    session = get_session()
    parts = equipment_id.split("-")
    equipment_type = parts[-1] if len(parts) > 1 else equipment_id
    query_text = f"OEM operating limits for {equipment_type} equipment, operating limit threshold maximum temperature vibration symptom root cause error corrective action"
    terms = _query_terms(query_text)
    if not terms:
        return _FALLBACK_OEM_TEXT

    conditions = "OR ".join(f'"CHUNK_TEXT"ILIKE \'%{t}%\'' for t in terms)
    sql = f'SELECT "FILE_NAME", "CHUNK_INDEX", "CHUNK_TEXT"FROM {CHUNK_TABLE} WHERE {conditions} LIMIT 10'

    try:
        df = session.sql(sql).to_pandas()
        if df.empty:
            return _FALLBACK_OEM_TEXT

        rows = df.to_dict("records")
        ranked = sorted(
            rows,
            key=lambda r: sum(1 for t in terms if t in r["CHUNK_TEXT"].lower()),
            reverse=True,
        )[:2]

        return " | ".join(r["CHUNK_TEXT"][:300] for r in ranked)
    except Exception:
        return _FALLBACK_OEM_TEXT


def _classify_priority(rul_hours):
    if rul_hours <= 6:
        return "IMMEDIATE_MAINTENANCE", "CRITICAL"
    elif rul_hours <= 24:
        return "SCHEDULE_MAINTENANCE", "HIGH"
    return "MONITOR_EQUIPMENT", "MEDIUM"


def _query_cortex_analyst(question, context, status=None):
    session = get_session()
    equip_id = context.get("equipment_id")
    sku = context.get("sku")
    if equip_id and sku:
        analyst_prompt = f"For Equipment {equip_id} and SKU {sku}: {question}"
    else:
        analyst_prompt = question

    if status:
        status.write("**Cortex Analyst:** Generating SQL from natural language...")

    try:
        payload = json.dumps({
            "messages": [{"role": "user", "content": [{"type": "text", "text": analyst_prompt}]}],
            "semantic_model_file": f"@{_DB}.{_SCHEMA}.SEMANTIC_MODELS_STAGE/factory_health_ontology.yaml",
        })

        result = session.sql(
            f"SELECT SNOWFLAKE.CORTEX.ANALYST(PARSE_JSON('{payload.replace(chr(39), chr(39)+chr(39))}'))"
        ).collect()

        response = json.loads(result[0][0]) if result else {}
        messages = response.get("message", {}).get("content", [])
        sql_query = ""
        text_response = ""

        for msg in messages:
            if msg.get("type") == "sql":
                sql_query = msg.get("statement", "")
            elif msg.get("type") == "text":
                text_response += msg.get("text", "") + "\n"

        if sql_query:
            if status:
                status.write(f"**Snowflake:** Executing:\n```sql\n{sql_query}\n```")
            data_df = session.sql(sql_query).to_pandas()
            if status:
                status.write("**LLM:** Summarizing results...")
            summary = sis_llm.complete(
                session,
                f"Summarize this data for the user who asked: '{question}'\nData: {data_df.head(50).to_string()}",
            )
            return summary + "\n\n---\n*Answered by: **Snowflake Cortex Analyst + Cortex LLM***"

        return text_response or "Cortex Analyst did not return a result."

    except Exception as e:
        if status:
            status.write(f"Cortex Analyst failed: {e}. Falling back to LLM Text-to-SQL...")
        return _fallback_text_to_sql(session, question, status)


def _fallback_text_to_sql(session, question, status=None):
    yaml_desc = (
        "Tables: IT_OT_CONVERGED (TIMESTAMP, EQUIPMENT_ID, TEMPERATURE_C, VIBRATION_RMS, BATCH_ID, SKU_ID), "
        "ASSET_RUL_PREDICTIONS (EQUIPMENT_ID, PREDICTED_FAILURE_TIMESTAMP, RUL_HOURS, PREDICTIVE_CAUSE). "
        "Database: OEE_COMMAND_CENTER, Schema: FACTORY_FLOOR."
    )
    sql_prompt = (
        f"You are a Snowflake SQL expert. Given this schema: {yaml_desc}\n\n"
        f"User question: {question}\n\nReturn ONLY the raw SQL query, no markdown."
    )
    try:
        sql_query = sis_llm.complete(session, sql_prompt).strip()
        if sql_query.startswith("```"):
            sql_query = re.sub(r"^```\w*\n?|```$", "", sql_query).strip()
        if status:
            status.write(f"**LLM Fallback SQL:**\n```sql\n{sql_query}\n```")
        data_df = session.sql(sql_query).to_pandas()
        summary = sis_llm.complete(
            session,
            f"Summarize for user who asked '{question}':\n{data_df.head(50).to_string()}",
        )
        return summary + "\n\n---\n*Answered by: **Cortex LLM** (Fallback Text-to-SQL)*"
    except Exception as e:
        return f"**LLM Fallback Error:** {e}"


def ask_investigative_agent(question, context, status=None):
    session = get_session()
    rul = context.get("rul", "Unknown")

    if status:
        status.write("**LLM:** Classifying request intent...")
    try:
        intent = sis_llm.complete(
            session,
            f"Classify as 'DATA' or 'MANUAL' (one word only): {question}",
        ).strip().upper()
        if status:
            status.write(f"**Router:** Intent = `{intent}`")
    except Exception:
        intent = "MANUAL"

    if "DATA" in intent:
        return _query_cortex_analyst(question, context, status=status)

    if status:
        status.write("Retrieving OEM manuals...")

    oem_constraints = _retrieve_oem_constraints(context.get("equipment_id", ""))

    try:
        action, priority = _classify_priority(float(rul))
    except (TypeError, ValueError):
        action, priority = "MONITOR_EQUIPMENT", "MEDIUM"

    prompt = f"""You are an Investigative AI Agent for a manufacturing plant.

<Investigation Data>
- Equipment ID: {context.get('equipment_id', 'Unknown')}
- Active SKU: {context.get('sku', 'Unknown')}
- Predicted RUL: {rul} hours
- Action: {action}
- Priority: {priority}
- OEM Constraints: {oem_constraints}
</Investigation Data>

User Question: {question}

Answer concisely, referencing the OEM limits and RUL."""

    if status:
        status.write("**LLM:** Generating answer...")
    answer = sis_llm.complete(session, prompt)
    return answer + "\n\n---\n*Answered by: **Snowflake Cortex LLM***"


def post_slack_alert(channel, text):
    """Post to Slack via Snowflake Webhook Notification Integration."""
    session = get_session()
    try:
        safe_text = text.replace("'", "''")
        session.sql(f"""
            CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(
                SNOWFLAKE.NOTIFICATION.TEXT_PLAIN('{safe_text}'),
                SNOWFLAKE.NOTIFICATION.INTEGRATION('SLACK_WEBHOOK_INT')
            )
        """).collect()
        return True
    except Exception as e:
        st.warning(f"Slack dispatch failed: {e}")
        return False
