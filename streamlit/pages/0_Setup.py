"""One-click infrastructure setup wizard for OEE Command Center."""
import streamlit as st
from snowflake_conn import get_session

st.set_page_config(page_title="Setup", layout="wide")
st.title("Infrastructure Setup Wizard")
st.markdown("Deploy the entire OEE Command Center pipeline with one click.")

session = get_session()

# ── Phase definitions ────────────────────────────────────────────────────────

def _phase_done(check_sql):
    try:
        result = session.sql(check_sql).collect()
        return bool(result and result[0][0] and int(result[0][0]) > 0)
    except Exception:
        return False


def phase_1_infrastructure(status):
    status.update(label="Phase 1/8: Creating tables, stages, dynamic table...", state="running")
    sqls = [
        "CREATE DATABASE IF NOT EXISTS OEE_COMMAND_CENTER",
        "CREATE SCHEMA IF NOT EXISTS OEE_COMMAND_CENTER.FACTORY_FLOOR",
        "USE DATABASE OEE_COMMAND_CENTER",
        "USE SCHEMA FACTORY_FLOOR",
        """CREATE TABLE IF NOT EXISTS RAW_IT_BATCHES (
            BATCH_ID VARCHAR, SKU_ID VARCHAR, EQUIPMENT_ID VARCHAR,
            START_TIME TIMESTAMP_NTZ, END_TIME TIMESTAMP_NTZ)""",
        """CREATE TABLE IF NOT EXISTS RAW_OT_TELEMETRY (
            TIMESTAMP TIMESTAMP_NTZ, EQUIPMENT_ID VARCHAR,
            TEMPERATURE_C FLOAT, VIBRATION_RMS FLOAT)""",
        """CREATE OR REPLACE FILE FORMAT CSV_FORMAT
            TYPE = 'CSV' FIELD_OPTIONALLY_ENCLOSED_BY = '"' SKIP_HEADER = 1""",
        "CREATE STAGE IF NOT EXISTS FACTORY_DATA_STAGE FILE_FORMAT = CSV_FORMAT",
        """CREATE OR REPLACE STAGE OEM_MANUALS_STAGE
            DIRECTORY = (ENABLE = TRUE) ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')""",
        "CREATE STAGE IF NOT EXISTS SEMANTIC_MODELS_STAGE DIRECTORY = (ENABLE = TRUE)",
        """CREATE TABLE IF NOT EXISTS OEM_EQUIPMENT_THRESHOLDS (
            EQUIPMENT_ID VARCHAR, SKU_ID VARCHAR,
            MAX_TEMP_LIMIT FLOAT, MAX_VIBRATION_LIMIT FLOAT,
            LAST_UPDATED TIMESTAMP DEFAULT CURRENT_TIMESTAMP())""",
        """CREATE TABLE IF NOT EXISTS OEM_MANUAL_CHUNKS (
            FILE_NAME VARCHAR, CHUNK_INDEX INTEGER, CHUNK_TEXT VARCHAR)""",
        """CREATE TABLE IF NOT EXISTS ALERTS_HISTORY (
            TIMESTAMP TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            EQUIPMENT_ID VARCHAR, SKU_ID VARCHAR, ACTION_TAKEN VARCHAR,
            PRIORITY VARCHAR, RUL_HOURS FLOAT, OEM_CONSTRAINTS VARCHAR, STATUS VARCHAR)""",
    ]
    for s in sqls:
        session.sql(s).collect()

    # Dynamic table (CREATE OR REPLACE is idempotent)
    session.sql("""
        CREATE OR REPLACE DYNAMIC TABLE OEE_COMMAND_CENTER.FACTORY_FLOOR.IT_OT_CONVERGED
            TARGET_LAG = '1 minute' WAREHOUSE = 'COMPUTE_WH'
        AS SELECT
            ot.TIMESTAMP, ot.EQUIPMENT_ID, ot.TEMPERATURE_C, ot.VIBRATION_RMS,
            COALESCE(it.BATCH_ID, 'CHANGEOVER/IDLE') AS BATCH_ID,
            COALESCE(it.SKU_ID, 'NONE') AS SKU_ID
        FROM RAW_OT_TELEMETRY ot
        LEFT JOIN RAW_IT_BATCHES it
            ON ot.EQUIPMENT_ID = it.EQUIPMENT_ID
            AND ot.TIMESTAMP BETWEEN it.START_TIME AND it.END_TIME
    """).collect()
    status.update(label="Phase 1/8: Infrastructure created", state="complete")


def phase_2_synthetic_data(status):
    status.update(label="Phase 2/8: Generating synthetic IT/OT data...", state="running")

    # Check if data already exists
    if _phase_done("SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_OT_TELEMETRY"):
        status.update(label="Phase 2/8: Data already exists (skipped)", state="complete")
        return

    session.sql("USE DATABASE OEE_COMMAND_CENTER").collect()
    session.sql("USE SCHEMA FACTORY_FLOOR").collect()

    # Generate OT telemetry: 5 equipment lines x 7 days x ~1440 readings/day = ~50,400 rows
    session.sql("""
        INSERT INTO RAW_OT_TELEMETRY (TIMESTAMP, EQUIPMENT_ID, TEMPERATURE_C, VIBRATION_RMS)
        WITH equipment AS (
            SELECT column1 AS EQUIPMENT_ID FROM VALUES
                ('LINE-1-MIXING'), ('LINE-2-PACKAGING'), ('LINE-3-FILLING'),
                ('LINE-4-LABELING'), ('LINE-5-PALLETIZING')
        ),
        minutes AS (
            SELECT DATEADD('MINUTE', -SEQ4(), CURRENT_TIMESTAMP()) AS TS
            FROM TABLE(GENERATOR(ROWCOUNT => 10080))
        )
        SELECT
            m.TS,
            e.EQUIPMENT_ID,
            ROUND(70 + 15 * SIN(EXTRACT(HOUR FROM m.TS) * 0.26) +
                  UNIFORM(-3.0, 3.0, RANDOM()) +
                  CASE WHEN e.EQUIPMENT_ID = 'LINE-2-PACKAGING' THEN 8 ELSE 0 END, 2),
            ROUND(1.8 + 0.5 * SIN(EXTRACT(HOUR FROM m.TS) * 0.13) +
                  UNIFORM(-0.2, 0.2, RANDOM()) +
                  CASE WHEN e.EQUIPMENT_ID = 'LINE-2-PACKAGING' THEN 0.4 ELSE 0 END, 3)
        FROM equipment e CROSS JOIN minutes m
    """).collect()

    # Generate IT batch schedule: ~210 batches (5 lines x 6 batches/day x 7 days)
    session.sql("""
        INSERT INTO RAW_IT_BATCHES (BATCH_ID, SKU_ID, EQUIPMENT_ID, START_TIME, END_TIME)
        WITH equipment AS (
            SELECT column1 AS EQUIPMENT_ID FROM VALUES
                ('LINE-1-MIXING'), ('LINE-2-PACKAGING'), ('LINE-3-FILLING'),
                ('LINE-4-LABELING'), ('LINE-5-PALLETIZING')
        ),
        skus AS (
            SELECT column1 AS SKU_ID FROM VALUES ('SKU-100'), ('SKU-500'), ('SKU-899')
        ),
        batch_seq AS (
            SELECT SEQ4() AS BATCH_NUM FROM TABLE(GENERATOR(ROWCOUNT => 42))
        )
        SELECT
            CONCAT('B-', e.EQUIPMENT_ID, '-', b.BATCH_NUM) AS BATCH_ID,
            s.SKU_ID,
            e.EQUIPMENT_ID,
            DATEADD('MINUTE', -(b.BATCH_NUM * 240 + UNIFORM(0, 60, RANDOM())),
                    CURRENT_TIMESTAMP()) AS START_TIME,
            DATEADD('MINUTE', -(b.BATCH_NUM * 240 + UNIFORM(0, 60, RANDOM()) - UNIFORM(120, 220, RANDOM())),
                    CURRENT_TIMESTAMP()) AS END_TIME
        FROM equipment e
        CROSS JOIN batch_seq b
        CROSS JOIN skus s
        WHERE MOD(b.BATCH_NUM + ABS(HASH(e.EQUIPMENT_ID)), 3) = MOD(ABS(HASH(s.SKU_ID)), 3)
    """).collect()

    status.update(label="Phase 2/8: Synthetic data generated", state="complete")


def phase_3_refresh_dt(status):
    status.update(label="Phase 3/8: Refreshing dynamic table...", state="running")
    session.sql("ALTER DYNAMIC TABLE OEE_COMMAND_CENTER.FACTORY_FLOOR.IT_OT_CONVERGED REFRESH").collect()
    status.update(label="Phase 3/8: Dynamic table refreshed", state="complete")


def phase_4_thresholds(status):
    status.update(label="Phase 4/8: Inserting OEM thresholds...", state="running")
    if _phase_done("SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_EQUIPMENT_THRESHOLDS"):
        status.update(label="Phase 4/8: Thresholds already exist (skipped)", state="complete")
        return
    session.sql("""
        INSERT INTO OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_EQUIPMENT_THRESHOLDS
            (EQUIPMENT_ID, SKU_ID, MAX_TEMP_LIMIT, MAX_VIBRATION_LIMIT) VALUES
            ('ALL', 'ALL', 85, 2.35),
            ('LINE-2-PACKAGING', 'SKU-100', 70, 2.5),
            ('LINE-2-PACKAGING', 'SKU-500', 75, 3),
            ('LINE-2-PACKAGING', 'SKU-899', 82, 4.2)
    """).collect()
    status.update(label="Phase 4/8: OEM thresholds inserted", state="complete")


def phase_5_parse_pdf(status):
    status.update(label="Phase 5/8: Parsing OEM manual PDF...", state="running")
    if _phase_done("SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS"):
        status.update(label="Phase 5/8: OEM chunks already exist (skipped)", state="complete")
        return

    # Check if PDF is on stage
    try:
        files = session.sql("LIST @OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUALS_STAGE").collect()
        pdf_found = any("OEM_Maintenance" in str(r[0]) for r in files)
    except Exception:
        pdf_found = False

    if not pdf_found:
        status.update(label="Phase 5/8: OEM PDF not found on stage (skipped — upload via CI/CD or manually)", state="error")
        return

    session.sql("""
        INSERT INTO OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS (FILE_NAME, CHUNK_INDEX, CHUNK_TEXT)
        WITH parsed AS (
            SELECT SNOWFLAKE.CORTEX.PARSE_DOCUMENT(
                '@OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUALS_STAGE',
                'OEM_Maintenance_and_Operations_Manual.pdf',
                {'mode': 'LAYOUT'}
            ) AS parsed_data
        ),
        full_text AS (
            SELECT parsed_data:content::VARCHAR AS content_text FROM parsed
        ),
        numbers AS (
            SELECT SEQ4() AS n FROM TABLE(GENERATOR(ROWCOUNT => 100))
        ),
        chunks AS (
            SELECT
                'OEM_Maintenance_and_Operations_Manual.pdf' AS FILE_NAME,
                n AS CHUNK_INDEX,
                SUBSTR(ft.content_text, n * 1700 + 1, 2000) AS CHUNK_TEXT
            FROM full_text ft CROSS JOIN numbers
            WHERE n * 1700 + 1 <= LENGTH(ft.content_text)
        )
        SELECT FILE_NAME, CHUNK_INDEX, CHUNK_TEXT FROM chunks
    """).collect()
    status.update(label="Phase 5/8: OEM manual parsed and chunked", state="complete")


def phase_6_cortex_search(status):
    status.update(label="Phase 6/8: Creating Cortex Search Service...", state="running")
    try:
        session.sql("""
            CREATE OR REPLACE CORTEX SEARCH SERVICE OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_SEARCH
                ON CHUNK_TEXT WAREHOUSE = COMPUTE_WH TARGET_LAG = '1 hour'
            AS SELECT FILE_NAME, CHUNK_INDEX, CHUNK_TEXT
               FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS
        """).collect()
        status.update(label="Phase 6/8: Cortex Search Service created", state="complete")
    except Exception as e:
        if "not supported" in str(e).lower() or "not available" in str(e).lower():
            status.update(label="Phase 6/8: Cortex Search not available on this account (skipped)", state="error")
        else:
            raise


def phase_7_ml_and_predictions(status):
    status.update(label="Phase 7/8: Training ML models & generating predictions...", state="running")
    session.sql("USE DATABASE OEE_COMMAND_CENTER").collect()
    session.sql("USE SCHEMA FACTORY_FLOOR").collect()

    session.sql("""
        CREATE OR REPLACE VIEW V_EQUIPMENT_TEMP_HISTORY AS
        SELECT DATE_TRUNC('HOUR', TIMESTAMP) AS TS_HOUR, EQUIPMENT_ID, AVG(TEMPERATURE_C) AS AVG_TEMP
        FROM IT_OT_CONVERGED GROUP BY 1, 2
    """).collect()

    session.sql("""
        CREATE OR REPLACE VIEW V_EQUIPMENT_VIBE_HISTORY AS
        SELECT DATE_TRUNC('HOUR', TIMESTAMP) AS TS_HOUR, EQUIPMENT_ID, AVG(VIBRATION_RMS) AS AVG_VIBE
        FROM IT_OT_CONVERGED GROUP BY 1, 2
    """).collect()

    session.sql("""
        CREATE OR REPLACE SNOWFLAKE.ML.FORECAST EQUIPMENT_TEMP_FORECAST (
            INPUT_DATA => SYSTEM$REFERENCE('VIEW', 'V_EQUIPMENT_TEMP_HISTORY'),
            SERIES_COLNAME => 'EQUIPMENT_ID', TIMESTAMP_COLNAME => 'TS_HOUR', TARGET_COLNAME => 'AVG_TEMP')
    """).collect()

    session.sql("""
        CREATE OR REPLACE SNOWFLAKE.ML.FORECAST EQUIPMENT_VIBE_FORECAST (
            INPUT_DATA => SYSTEM$REFERENCE('VIEW', 'V_EQUIPMENT_VIBE_HISTORY'),
            SERIES_COLNAME => 'EQUIPMENT_ID', TIMESTAMP_COLNAME => 'TS_HOUR', TARGET_COLNAME => 'AVG_VIBE')
    """).collect()

    session.sql("""
        CREATE OR REPLACE TABLE PREDICTED_TEMPERATURES AS
        SELECT * FROM TABLE(EQUIPMENT_TEMP_FORECAST!FORECAST(FORECASTING_PERIODS => 72))
    """).collect()

    session.sql("""
        CREATE OR REPLACE TABLE PREDICTED_VIBRATIONS AS
        SELECT * FROM TABLE(EQUIPMENT_VIBE_FORECAST!FORECAST(FORECASTING_PERIODS => 72))
    """).collect()

    session.sql("""
        CREATE OR REPLACE VIEW ASSET_RUL_PREDICTIONS AS
        WITH active_sku AS (
            SELECT EQUIPMENT_ID, SKU_ID FROM IT_OT_CONVERGED
            WHERE BATCH_ID != 'CHANGEOVER/IDLE'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY EQUIPMENT_ID ORDER BY TIMESTAMP DESC) = 1
        ),
        sku_thresholds AS (
            SELECT a.EQUIPMENT_ID,
                COALESCE(s.MAX_TEMP_LIMIT, e.MAX_TEMP_LIMIT, g.MAX_TEMP_LIMIT, 90.0) AS TEMP_THRESHOLD,
                COALESCE(s.MAX_VIBRATION_LIMIT, e.MAX_VIBRATION_LIMIT, g.MAX_VIBRATION_LIMIT, 2.3) AS VIBE_THRESHOLD
            FROM active_sku a
            LEFT JOIN OEM_EQUIPMENT_THRESHOLDS s ON a.EQUIPMENT_ID = s.EQUIPMENT_ID AND a.SKU_ID = s.SKU_ID
            LEFT JOIN OEM_EQUIPMENT_THRESHOLDS e ON a.EQUIPMENT_ID = e.EQUIPMENT_ID AND e.SKU_ID = 'ALL'
            LEFT JOIN OEM_EQUIPMENT_THRESHOLDS g ON g.EQUIPMENT_ID = 'ALL' AND g.SKU_ID = 'ALL'
        ),
        temp_rul AS (
            SELECT p.SERIES AS EQUIPMENT_ID, MIN(p.TS) AS PREDICTED_FAILURE_TIMESTAMP,
                GREATEST(1, ROUND(TIMEDIFF('HOUR', CURRENT_TIMESTAMP(), MIN(p.TS)), 1)) AS RUL_HOURS,
                'Temperature Forecast Breach' AS PREDICTIVE_CAUSE
            FROM PREDICTED_TEMPERATURES p
            INNER JOIN sku_thresholds t ON p.SERIES = t.EQUIPMENT_ID
            WHERE p.FORECAST >= t.TEMP_THRESHOLD GROUP BY p.SERIES
        ),
        vibe_rul AS (
            SELECT p.SERIES AS EQUIPMENT_ID, MIN(p.TS) AS PREDICTED_FAILURE_TIMESTAMP,
                GREATEST(1, ROUND(TIMEDIFF('HOUR', CURRENT_TIMESTAMP(), MIN(p.TS)), 1)) AS RUL_HOURS,
                'Vibration Forecast Breach' AS PREDICTIVE_CAUSE
            FROM PREDICTED_VIBRATIONS p
            INNER JOIN sku_thresholds t ON p.SERIES = t.EQUIPMENT_ID
            WHERE p.FORECAST >= t.VIBE_THRESHOLD GROUP BY p.SERIES
        ),
        combined_rul AS (
            SELECT * FROM temp_rul UNION ALL SELECT * FROM vibe_rul
        )
        SELECT EQUIPMENT_ID, PREDICTED_FAILURE_TIMESTAMP, RUL_HOURS, PREDICTIVE_CAUSE
        FROM combined_rul
        QUALIFY ROW_NUMBER() OVER (PARTITION BY EQUIPMENT_ID ORDER BY RUL_HOURS ASC) = 1
    """).collect()

    status.update(label="Phase 7/8: ML models trained, predictions generated, RUL view created", state="complete")


def phase_8_slack_webhook(status):
    status.update(label="Phase 8/8: Checking Slack webhook integration...", state="running")
    try:
        result = session.sql("SHOW NOTIFICATION INTEGRATIONS LIKE 'SLACK_WEBHOOK_INT'").collect()
        if result:
            status.update(label="Phase 8/8: Slack webhook integration exists", state="complete")
        else:
            status.update(label="Phase 8/8: No Slack webhook configured (run sql/05-sis-external-access.sql to enable)", state="error")
    except Exception:
        status.update(label="Phase 8/8: Slack webhook check skipped (configure manually if needed)", state="error")


# ── UI ────────────────────────────────────────────────────────────────────────

st.markdown("---")

col1, col2 = st.columns([1, 2])
with col1:
    deploy_btn = st.button("Deploy Infrastructure", type="primary", use_container_width=True)
with col2:
    st.caption("Creates all Snowflake objects, generates synthetic data, trains ML models, and configures the pipeline.")

if deploy_btn:
    phases = [
        ("Infrastructure", phase_1_infrastructure),
        ("Synthetic Data", phase_2_synthetic_data),
        ("Dynamic Table Refresh", phase_3_refresh_dt),
        ("OEM Thresholds", phase_4_thresholds),
        ("OEM Manual Parse", phase_5_parse_pdf),
        ("Cortex Search", phase_6_cortex_search),
        ("ML Models & Predictions", phase_7_ml_and_predictions),
        ("Slack Webhook", phase_8_slack_webhook),
    ]

    progress = st.progress(0, text="Starting deployment...")
    statuses = []
    for name, _ in phases:
        statuses.append(st.status(f"{name}...", expanded=False))

    errors = []
    for i, (name, func) in enumerate(phases):
        progress.progress((i) / len(phases), text=f"Running: {name}...")
        try:
            func(statuses[i])
        except Exception as e:
            statuses[i].update(label=f"{name}: FAILED — {e}", state="error")
            errors.append(f"{name}: {e}")

    progress.progress(1.0, text="Deployment complete!")

    if errors:
        st.warning(f"{len(errors)} phase(s) had issues. The app may still work for phases that succeeded.")
        for err in errors:
            st.error(err)
    else:
        st.success("All phases completed successfully! Navigate to the **Dashboard** page to see your data.")
        st.balloons()

# ── Current Status ───────────────────────────────────────────────────────────

st.markdown("---")
st.subheader("Current Deployment Status")

checks = {
    "RAW_OT_TELEMETRY": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_OT_TELEMETRY",
    "RAW_IT_BATCHES": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_IT_BATCHES",
    "IT_OT_CONVERGED": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.IT_OT_CONVERGED",
    "OEM_EQUIPMENT_THRESHOLDS": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_EQUIPMENT_THRESHOLDS",
    "OEM_MANUAL_CHUNKS": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS",
    "PREDICTED_TEMPERATURES": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.PREDICTED_TEMPERATURES",
    "ASSET_RUL_PREDICTIONS": "SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.ASSET_RUL_PREDICTIONS",
}

cols = st.columns(4)
for idx, (name, sql) in enumerate(checks.items()):
    with cols[idx % 4]:
        try:
            result = session.sql(sql).collect()
            count = int(result[0][0]) if result else 0
            if count > 0:
                st.metric(name, f"{count:,} rows")
            else:
                st.metric(name, "Empty")
        except Exception:
            st.metric(name, "Not found")
