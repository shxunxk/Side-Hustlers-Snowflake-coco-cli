# SKU-Specific OEE Degradation Tracker ΓÇö Build Plan

Incremental, phase-by-phase guide to build the full prototype using CoCo CLI.
Each phase is self-contained and can be validated before moving to the next.

---

## Prerequisites

- Snowflake account with ACCOUNTADMIN access
- Warehouse `COMPUTE_WH` available
- CoCo CLI installed and connected (`cortex connections list` shows your connection)
- Python 3.10+ with `pip`
- Python 3.10+ with `pip`
- Ollama installed locally (fallback LLM, optional)

---

## Phase 1 ΓÇö Snowflake Infrastructure

> Goal: create the database, schema, tables, stages, and file format.

Execute each statement from `sql/01-infrastructure.sql` in order:

```
-- 1.1 Database and schema
CREATE DATABASE IF NOT EXISTS OEE_COMMAND_CENTER;
USE DATABASE OEE_COMMAND_CENTER;
CREATE SCHEMA IF NOT EXISTS FACTORY_FLOOR;
USE SCHEMA FACTORY_FLOOR;

-- 1.2 Raw input tables
CREATE OR REPLACE TABLE RAW_IT_BATCHES (
    BATCH_ID VARCHAR,
    SKU_ID VARCHAR,
    EQUIPMENT_ID VARCHAR,
    START_TIME TIMESTAMP_NTZ,
    END_TIME TIMESTAMP_NTZ
);

CREATE OR REPLACE TABLE RAW_OT_TELEMETRY (
    TIMESTAMP TIMESTAMP_NTZ,
    EQUIPMENT_ID VARCHAR,
    TEMPERATURE_C FLOAT,
    VIBRATION_RMS FLOAT
);

-- 1.3 File format and data stage
CREATE OR REPLACE FILE FORMAT CSV_FORMAT
    TYPE = 'CSV'
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    SKIP_HEADER = 1;

CREATE OR REPLACE STAGE FACTORY_DATA_STAGE
    FILE_FORMAT = CSV_FORMAT;

-- 1.4 IT/OT convergence dynamic table
CREATE OR REPLACE DYNAMIC TABLE IT_OT_CONVERGED
    TARGET_LAG = '1 minute'
    WAREHOUSE = 'COMPUTE_WH'
AS
SELECT
    ## Phase 9 — CoCo Skill Validation (Optional)
);
    > Goal: validate native CoCo project skill discovery.
-- 1.6 Stages for OEM manuals and semantic models
    Skills are defined under `.cortex/skills/` and auto-discovered by CoCo when you
    work in this project directory.
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

CREATE STAGE IF NOT EXISTS SEMANTIC_MODELS_STAGE
    DIRECTORY = (ENABLE = TRUE);
```

### Phase 1 ΓÇö Validation

```
SHOW TABLES IN OEE_COMMAND_CENTER.FACTORY_FLOOR;
SHOW DYNAMIC TABLES IN OEE_COMMAND_CENTER.FACTORY_FLOOR;
SHOW STAGES IN OEE_COMMAND_CENTER.FACTORY_FLOOR;
-- Expect: 6 tables, 1 dynamic table, 3 stages (incl FACTORY_DATA_STAGE, OEM_MANUALS_STAGE, SEMANTIC_MODELS_STAGE)
```

---

## Phase 2 ΓÇö Synthetic Data Generation and Ingestion

> Goal: generate 7-day IT/OT datasets, upload to stage, load into tables.

### 2.1 Generate data

```bash
cd code/misc
python data_generator.py
```

This creates two CSVs in `data/`:
- `it_batch_schedule.csv` ΓÇö batch records for 5 equipment lines, 3 SKUs
- `ot_telemetry_stream.csv` ΓÇö 1-minute sensor readings with degradation curves

### 2.2 Upload CSVs to Snowflake stage

Via CoCo CLI or Snowsight worksheet:

```sql
PUT file://data/it_batch_schedule.csv @FACTORY_DATA_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT file://data/ot_telemetry_stream.csv @FACTORY_DATA_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

### 2.3 Load data

From `sql/02-data-ingestion.sql`:

```sql
COPY INTO RAW_IT_BATCHES
    FROM @FACTORY_DATA_STAGE/it_batch_schedule.csv
    FILE_FORMAT = CSV_FORMAT FORCE = TRUE;

COPY INTO RAW_OT_TELEMETRY
    FROM @FACTORY_DATA_STAGE/ot_telemetry_stream.csv
    FILE_FORMAT = CSV_FORMAT FORCE = TRUE;

ALTER DYNAMIC TABLE IT_OT_CONVERGED REFRESH;
```

### Phase 2 ΓÇö Validation

```sql
SELECT COUNT(*) FROM RAW_IT_BATCHES;           -- ~200+ rows
SELECT COUNT(*) FROM RAW_OT_TELEMETRY;         -- ~50,000 rows
SELECT COUNT(*) FROM IT_OT_CONVERGED;          -- should match RAW_OT_TELEMETRY
SELECT DISTINCT EQUIPMENT_ID FROM IT_OT_CONVERGED;
-- Expect 5 lines: LINE-1-MIXING through LINE-5-PALLETIZING
```

---

## Phase 3 ΓÇö ML Forecasts and Predictive Analytics

> Goal: build hourly aggregation views, train ML forecasts, generate predictions, create RUL view.

Execute from `sql/03-analytics.sql`:

```sql
USE DATABASE OEE_COMMAND_CENTER;
USE SCHEMA FACTORY_FLOOR;

-- 3.1 Hourly aggregation views
CREATE OR REPLACE VIEW V_EQUIPMENT_TEMP_HISTORY AS
SELECT
    DATE_TRUNC('HOUR', TIMESTAMP) AS TS_HOUR,
    EQUIPMENT_ID,
    AVG(TEMPERATURE_C) AS AVG_TEMP
FROM IT_OT_CONVERGED
GROUP BY 1, 2;

CREATE OR REPLACE VIEW V_EQUIPMENT_VIBE_HISTORY AS
SELECT
    DATE_TRUNC('HOUR', TIMESTAMP) AS TS_HOUR,
    EQUIPMENT_ID,
    AVG(VIBRATION_RMS) AS AVG_VIBE
FROM IT_OT_CONVERGED
GROUP BY 1, 2;

-- 3.2 Train ML forecast models
CREATE OR REPLACE SNOWFLAKE.ML.FORECAST EQUIPMENT_TEMP_FORECAST (
    INPUT_DATA => SYSTEM$REFERENCE('VIEW', 'V_EQUIPMENT_TEMP_HISTORY'),
    SERIES_COLNAME => 'EQUIPMENT_ID',
    TIMESTAMP_COLNAME => 'TS_HOUR',
    TARGET_COLNAME => 'AVG_TEMP'
);

CREATE OR REPLACE SNOWFLAKE.ML.FORECAST EQUIPMENT_VIBE_FORECAST (
    INPUT_DATA => SYSTEM$REFERENCE('VIEW', 'V_EQUIPMENT_VIBE_HISTORY'),
    SERIES_COLNAME => 'EQUIPMENT_ID',
    TIMESTAMP_COLNAME => 'TS_HOUR',
    TARGET_COLNAME => 'AVG_VIBE'
);

-- 3.3 Generate 72-hour forecasts
CREATE OR REPLACE TABLE PREDICTED_TEMPERATURES AS
SELECT * FROM TABLE(EQUIPMENT_TEMP_FORECAST!FORECAST(FORECASTING_PERIODS => 72));

CREATE OR REPLACE TABLE PREDICTED_VIBRATIONS AS
SELECT * FROM TABLE(EQUIPMENT_VIBE_FORECAST!FORECAST(FORECASTING_PERIODS => 72));

-- 3.4 SKU-aware RUL predictions view
CREATE OR REPLACE VIEW ASSET_RUL_PREDICTIONS AS
WITH active_sku AS (
    SELECT EQUIPMENT_ID, SKU_ID
    FROM IT_OT_CONVERGED
    WHERE BATCH_ID != 'CHANGEOVER/IDLE'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY EQUIPMENT_ID ORDER BY TIMESTAMP DESC) = 1
),
sku_thresholds AS (
    SELECT
        a.EQUIPMENT_ID,
        COALESCE(s.MAX_TEMP_LIMIT, e.MAX_TEMP_LIMIT, g.MAX_TEMP_LIMIT, 90.0) AS TEMP_THRESHOLD,
        COALESCE(s.MAX_VIBRATION_LIMIT, e.MAX_VIBRATION_LIMIT, g.MAX_VIBRATION_LIMIT, 2.3) AS VIBE_THRESHOLD
    FROM active_sku a
    LEFT JOIN OEM_EQUIPMENT_THRESHOLDS s
        ON a.EQUIPMENT_ID = s.EQUIPMENT_ID AND a.SKU_ID = s.SKU_ID
    LEFT JOIN OEM_EQUIPMENT_THRESHOLDS e
        ON a.EQUIPMENT_ID = e.EQUIPMENT_ID AND e.SKU_ID = 'ALL'
    LEFT JOIN OEM_EQUIPMENT_THRESHOLDS g
        ON g.EQUIPMENT_ID = 'ALL' AND g.SKU_ID = 'ALL'
),
temp_rul AS (
    SELECT
        p.SERIES AS EQUIPMENT_ID,
        MIN(p.TS) AS PREDICTED_FAILURE_TIMESTAMP,
        GREATEST(1, ROUND(TIMEDIFF('HOUR', CURRENT_TIMESTAMP(), MIN(p.TS)), 1)) AS RUL_HOURS,
        'Temperature Forecast Breach' AS PREDICTIVE_CAUSE
    FROM PREDICTED_TEMPERATURES p
    INNER JOIN sku_thresholds t ON p.SERIES = t.EQUIPMENT_ID
    WHERE p.FORECAST >= t.TEMP_THRESHOLD
    GROUP BY p.SERIES
),
vibe_rul AS (
    SELECT
        p.SERIES AS EQUIPMENT_ID,
        MIN(p.TS) AS PREDICTED_FAILURE_TIMESTAMP,
        GREATEST(1, ROUND(TIMEDIFF('HOUR', CURRENT_TIMESTAMP(), MIN(p.TS)), 1)) AS RUL_HOURS,
        'Vibration Forecast Breach' AS PREDICTIVE_CAUSE
    FROM PREDICTED_VIBRATIONS p
    INNER JOIN sku_thresholds t ON p.SERIES = t.EQUIPMENT_ID
    WHERE p.FORECAST >= t.VIBE_THRESHOLD
    GROUP BY p.SERIES
),
combined_rul AS (
    SELECT * FROM temp_rul
    UNION ALL
    SELECT * FROM vibe_rul
)
SELECT
    EQUIPMENT_ID,
    PREDICTED_FAILURE_TIMESTAMP,
    RUL_HOURS,
    PREDICTIVE_CAUSE
FROM combined_rul
QUALIFY ROW_NUMBER() OVER (PARTITION BY EQUIPMENT_ID ORDER BY RUL_HOURS ASC) = 1;
```

### Phase 3 ΓÇö Validation

```sql
SELECT COUNT(*) FROM PREDICTED_TEMPERATURES;   -- 360 (5 equipment x 72 hours)
SELECT COUNT(*) FROM PREDICTED_VIBRATIONS;     -- 360
SELECT * FROM ASSET_RUL_PREDICTIONS ORDER BY RUL_HOURS;
-- Expect: 3-5 equipment rows with RUL, cause, and predicted failure timestamp
-- LINE-5-PALLETIZING may be absent (no breach in 72h window)
SHOW SNOWFLAKE.ML.FORECAST IN OEE_COMMAND_CENTER.FACTORY_FLOOR;
-- Expect: 2 models (EQUIPMENT_TEMP_FORECAST, EQUIPMENT_VIBE_FORECAST)
```

---

## Phase 4 ΓÇö OEM RAG Pipeline (PDF Ingestion + Cortex Search)

> Goal: upload OEM manual, parse via Cortex, chunk, extract thresholds, create search service.

### 4.1 Upload OEM PDF to stage

```bash
cd code/create_rag
python insert_document.py
```

Or via SQL:

```sql
PUT file://data/OEM_Maintenance_and_Operations_Manual.pdf
    @OEM_MANUALS_STAGE
    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

### 4.2 Parse PDF and extract thresholds

```bash
python parse_pdf.py
```

This does:
1. Calls `SNOWFLAKE.CORTEX.PARSE_DOCUMENT()` in LAYOUT mode on the staged PDF
2. Chunks the extracted text (2000-char windows, 300-char overlap)
3. Inserts chunks into `OEM_MANUAL_CHUNKS`
4. Sends chunk text to LLM to extract per-SKU thresholds
5. Inserts extracted thresholds into `OEM_EQUIPMENT_THRESHOLDS`

If LLM extraction fails, seed thresholds manually:

```sql
INSERT INTO OEM_EQUIPMENT_THRESHOLDS (EQUIPMENT_ID, SKU_ID, MAX_TEMP_LIMIT, MAX_VIBRATION_LIMIT) VALUES
('LINE-2-PACKAGING', 'SKU-100', 70.0, 2.5),
('LINE-2-PACKAGING', 'SKU-500', 75.0, 3.0),
('LINE-2-PACKAGING', 'SKU-899', 82.0, 4.2),
('ALL', 'ALL', 90.0, 2.3);
```

### 4.3 Create Cortex Search Service

From `sql/04-cortex-search.sql`:

```sql
USE WAREHOUSE COMPUTE_WH;

CREATE OR REPLACE CORTEX SEARCH SERVICE OEM_MANUAL_SEARCH
    ON CHUNK_TEXT
    WAREHOUSE = COMPUTE_WH
    TARGET_LAG = '1 hour'
    AS
    SELECT
        FILE_NAME,
        CHUNK_INDEX,
        CHUNK_TEXT
    FROM OEM_MANUAL_CHUNKS;
```

**Note:** This requires a non-trial Snowflake account. Trial accounts will fail
with `EMBED_TEXT_768 is not available for trial accounts`. The application
works without it (falls back to keyword ILIKE search), but semantic search
will be unavailable.

### Phase 4 ΓÇö Validation

```sql
SELECT COUNT(*) FROM OEM_MANUAL_CHUNKS;              -- 4 chunks
SELECT * FROM OEM_EQUIPMENT_THRESHOLDS ORDER BY EQUIPMENT_ID, SKU_ID;
-- Expect: 4 rows (3 SKU-specific for LINE-2-PACKAGING + 1 global ALL/ALL)
SHOW CORTEX SEARCH SERVICES IN OEE_COMMAND_CENTER.FACTORY_FLOOR;
-- Expect: 1 service (OEM_MANUAL_SEARCH) in ACTIVE state
```

---

## Phase 5 ΓÇö Semantic Model Deployment (Cortex Analyst)

> Goal: deploy the semantic YAML to enable natural-language data queries.

### 5.1 Deploy semantic model

```bash
cd code/semantic_model_code
python semantic_model_deployment.py
```

Or via SQL PUT:

```sql
PUT file://semantic_models/factory_health_ontology.yaml
    @SEMANTIC_MODELS_STAGE
    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

### Phase 5 ΓÇö Validation

```sql
LIST @SEMANTIC_MODELS_STAGE;
-- Expect: factory_health_ontology.yaml listed
```

You can also validate with CoCo CLI:

```bash
cortex reflect semantic_models/factory_health_ontology.yaml --target-schema OEE_COMMAND_CENTER.FACTORY_FLOOR
```

---

## Phase 6 ΓÇö Python Environment Setup

> Goal: install all Python dependencies for the agent pipeline and Streamlit app.

### 6.1 Create virtual environment and install dependencies

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 6.2 Configure environment variables

Create `.env` in the project root:

```
SNOWFLAKE_ACCOUNT=<your-account-identifier>
SNOWFLAKE_USER=<your-username>
SNOWFLAKE_PASSWORD=<your-password>
SNOWFLAKE_ROLE=ACCOUNTADMIN
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=OEE_COMMAND_CENTER
SNOWFLAKE_SCHEMA=FACTORY_FLOOR

# Optional: Slack MCP integration
SLACK_BOT_TOKEN=xoxb-...
SLACK_TEAM_ID=T...
SLACK_CHANNEL=#oee-production-alerts
```

### 6.3 Optional: Ollama local LLM

The agent pipeline tries Snowflake Cortex AI first, then falls back to Ollama.
If you want the local fallback:

```bash
ollama pull llama3.1
ollama serve
```

### Phase 6 ΓÇö Validation

```bash
python -c "import streamlit, snowflake.connector, pydantic, pandas; print('OK')"
```

---

## Phase 7 ΓÇö Agent Pipeline Smoke Test

> Goal: validate the end-to-end detection workflow from CLI before launching the UI.

### 7.1 Run the prediction agent standalone

```bash
cd code/execute_detection
python prediction.py
```

Expected output: prediction result for LINE-2-PACKAGING with RUL, failure flag,
and dynamic threshold fetched from `OEM_EQUIPMENT_THRESHOLDS`.

### 7.2 Run the full detection workflow

```bash
python detection_workflow.py
```

Expected output: prediction ΓåÆ investigation ΓåÆ future prediction chain.
Confirms OEM retrieval, threshold lookup (3-tier), Pydantic schema validation,
and LLM mitigation all work end-to-end.

### Phase 7 ΓÇö Validation

Check the output for:
- `[Prediction Agent] Successfully fetched dynamic thresholds` ΓÇö confirms DB lookup works
- `Retrieved N chunk(s)` ΓÇö confirms OEM manual keyword search works
- Final JSON with `priority`, `action`, `justification`, `oem_validation` fields

---

## Phase 8 ΓÇö Streamlit Application

> Goal: launch the full Command Center UI.

### 8.1 Launch Streamlit

```bash
streamlit run code/streamlit_app/app.py
```

### 8.2 First-run wizard

On first launch, `app.py` checks for valid Snowflake credentials.
If `.env` is already configured, it skips the wizard and shows the Command Center.

### 8.3 Application pages

| Page | What to test |
|------|-------------|
| **Dashboard** | Live telemetry grid, RUL alert cards per equipment, OEM PDF upload sidebar |
| **Investigate** | Ask: "Why is LINE-2-PACKAGING overheating?" ΓÇö verifies OEM RAG + LLM |
| **Alerts History** | Shows rows from ALERTS_HISTORY table |
| **Data Analyst** | Ask: "What is the average temperature for LINE-1-MIXING?" ΓÇö verifies Cortex Analyst |

### Phase 8 ΓÇö Validation

- Dashboard shows 5 equipment cards with RUL values
- Investigation chat returns OEM-grounded answers with SKU-specific thresholds
- Data Analyst returns SQL + results from the semantic model

---

## Phase 9 ΓÇö MCP Slack Integration (Optional)

> Goal: enable automated Slack alerts via the Execution Agent.

### 9.1 Configure Slack

1. Create a Slack app at https://api.slack.com/apps
2. Add bot scopes: `chat:write`, `channels:read`
3. Install to your workspace
4. Copy the Bot OAuth Token to `.env` as `SLACK_BOT_TOKEN`
5. Set `SLACK_TEAM_ID` and `SLACK_CHANNEL`

### 9.2 Generate MCP config and test

```powershell
cd code/mcp
.\start-mcp-inspector.ps1
```

This generates `mcp.json` from `.env` and launches the MCP Inspector.

### 9.3 Test Slack dispatch

Run the detection workflow ΓÇö if Slack is configured, the Execution Agent will
post a mitigation alert to `#oee-production-alerts`.

### Phase 9 ΓÇö Validation

```sql
SELECT * FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.ALERTS_HISTORY ORDER BY TIMESTAMP DESC;
-- Expect: new row(s) with STATUS = 'ALERT_SENT'
```

---

## Phase 10 ΓÇö CoCo Skill Registration (Optional)

> Goal: register the IT/OT Time-Series Joiner as a native CoCo project skill.

The skill is already defined at `.cortex/skills/it-ot-timeseries-joiner/SKILL.md`.
It is auto-discovered by CoCo when you work in this project directory.

### Validation

Ask CoCo: "Join the OT sensor table with the IT batch schedule on EQUIPMENT_ID"
ΓÇö CoCo should route to the skill and apply the boundary-join pattern.

---

## Object Dependency Graph

```
Phase 1                    Phase 2                Phase 3              Phase 4
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ                   ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ               ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ             ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
OEE_COMMAND_CENTER         data_generator.py      V_EQUIPMENT_TEMP_    OEM PDF
  ΓööΓöÇ FACTORY_FLOOR           Γöé                     HISTORY             Γöé
       Γö£ΓöÇ RAW_IT_BATCHES ΓùäΓöÇΓöÇΓöÇΓöñ COPY INTO          V_EQUIPMENT_VIBE_   PARSE_DOCUMENT
       Γö£ΓöÇ RAW_OT_TELEMETRYΓùäΓöÇΓöÇΓöÿ                     HISTORY             Γöé
       Γöé       Γöé                                      Γöé                OEM_MANUAL_CHUNKS
       Γöé       ΓööΓöÇΓöÇΓû║ IT_OT_CONVERGED ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöñ                Γöé
       Γöé                Γöé                             Γöé                OEM_MANUAL_SEARCH
       Γöé                Γöé                    EQUIPMENT_TEMP_FORECAST    Γöé
       Γöé                Γöé                    EQUIPMENT_VIBE_FORECAST   OEM_EQUIPMENT_
       Γöé                Γöé                             Γöé                THRESHOLDS
       Γöé                Γöé                    PREDICTED_TEMPERATURES         Γöé
       Γöé                Γöé                    PREDICTED_VIBRATIONS           Γöé
       Γöé                Γöé                             Γöé                    Γöé
       Γöé                ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓû║ ASSET_RUL_PREDICTIONS ΓùäΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÿ
       Γöé
       ΓööΓöÇ ALERTS_HISTORY ΓùäΓöÇΓöÇ Phase 7/8 (agent pipeline writes here)
```

---

## Quick Reference ΓÇö Account Requirements

| Feature | Trial Account | Standard Account |
|---------|--------------|-----------------|
| Tables, views, dynamic tables | Yes | Yes |
| SNOWFLAKE.ML.FORECAST | Yes | Yes |
| CORTEX.PARSE_DOCUMENT | Yes | Yes |
| CORTEX.COMPLETE (LLM) | Yes | Yes |
| CORTEX SEARCH SERVICE | **No** (needs EMBED_TEXT_768) | Yes |
| Cortex Analyst REST API | Yes | Yes |
