"""
test_oee_tracker.py — Test suite for SKU-Specific OEE Degradation Tracker (SiS-only).

Covers:
  1. Pydantic schema guardrails
  2. Snowflake live data validation
  3. IT/OT join invariants
"""
import os
import sys

import pytest
import pandas as pd
from pydantic import ValidationError

# ── Path setup ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_STREAMLIT = os.path.join(_PROJECT_ROOT, "streamlit")

if _STREAMLIT not in sys.path:
    sys.path.insert(0, _STREAMLIT)

from schemas import DiagnosticState, OEMValidation, MitigationDecision


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pydantic Schema Guardrails
# ═══════════════════════════════════════════════════════════════════════════

class TestSchemaGuardrails:

    def test_diagnostic_state_valid(self):
        ds = DiagnosticState(
            equipment_id="LINE-2-PACKAGING",
            metric="temperature",
            current_val=95.0,
            dynamic_threshold=82.0,
            rul_hours=3.5,
            failure_flag=True,
            confidence_score=0.95,
        )
        assert ds.equipment_id == "LINE-2-PACKAGING"
        assert ds.failure_flag is True
        assert 0.0 <= ds.confidence_score <= 1.0

    def test_diagnostic_state_rejects_bad_confidence(self):
        with pytest.raises(ValidationError):
            DiagnosticState(
                equipment_id="X",
                metric="temperature",
                current_val=80.0,
                dynamic_threshold=90.0,
                rul_hours=10.0,
                failure_flag=False,
                confidence_score=1.5,
            )

    def test_mitigation_decision_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            MitigationDecision(
                priority="CRITICAL",
                action="IMMEDIATE_MAINTENANCE",
                justification="test",
                approved_for_dispatch=True,
                hallucinated_field="this should fail",
            )

    def test_mitigation_decision_rejects_invalid_priority(self):
        with pytest.raises(ValidationError):
            MitigationDecision(
                priority="URGENT",
                action="IMMEDIATE_MAINTENANCE",
                justification="test",
                approved_for_dispatch=True,
            )

    def test_oem_validation_accepts_valid(self):
        ov = OEMValidation(
            max_temp_limit=82.0,
            max_vibration_limit=4.2,
            citation_source="OEM_MANUAL_CHUNKS",
            breach_detected=True,
        )
        assert ov.breach_detected is True
        assert ov.max_temp_limit == 82.0

    def test_oem_validation_ignores_extra(self):
        ov = OEMValidation(
            max_temp_limit=90.0,
            max_vibration_limit=2.3,
            citation_source="test",
            breach_detected=False,
            some_extra_field="should be ignored",
        )
        assert ov.breach_detected is False

    def test_mitigation_decision_valid_all_priorities(self):
        for priority in ("CRITICAL", "HIGH", "MEDIUM"):
            md = MitigationDecision(
                priority=priority,
                action="MONITOR_EQUIPMENT",
                justification="test",
                approved_for_dispatch=False,
            )
            assert md.priority == priority


# ═══════════════════════════════════════════════════════════════════════════
# 2. Snowflake Live Data Validation
# ═══════════════════════════════════════════════════════════════════════════

def _sf_connection():
    """Create a Snowflake connection using environment variables or connection name."""
    import snowflake.connector
    conn_name = os.getenv("SNOWFLAKE_CONNECTION_NAME")
    if conn_name:
        return snowflake.connector.connect(connection_name=conn_name)
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database="OEE_COMMAND_CENTER",
        schema="FACTORY_FLOOR",
    )


def _sf_query(sql: str):
    conn = _sf_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        cur.close()
        conn.close()


def _snowflake_available():
    try:
        conn = _sf_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


snowflake = pytest.mark.skipif(
    not _snowflake_available(),
    reason="Snowflake connection not available",
)


@snowflake
class TestSnowflakeLive:

    def test_thresholds_have_sku_dimension(self):
        df = _sf_query(
            "SELECT COLUMN_NAME FROM OEE_COMMAND_CENTER.INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='FACTORY_FLOOR' AND TABLE_NAME='OEM_EQUIPMENT_THRESHOLDS' "
            "ORDER BY ORDINAL_POSITION"
        )
        cols = df["COLUMN_NAME"].tolist()
        assert "EQUIPMENT_ID" in cols
        assert "SKU_ID" in cols

    def test_rul_predictions_not_empty(self):
        df = _sf_query("SELECT COUNT(*) AS CNT FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.ASSET_RUL_PREDICTIONS")
        assert int(df.iloc[0]["CNT"]) > 0

    def test_it_ot_converged_row_parity(self):
        df = _sf_query(
            "SELECT "
            "(SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_OT_TELEMETRY) AS OT_ROWS, "
            "(SELECT COUNT(*) FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.IT_OT_CONVERGED) AS CONV_ROWS"
        )
        assert int(df.iloc[0]["OT_ROWS"]) == int(df.iloc[0]["CONV_ROWS"])

    def test_no_ot_row_duplication(self):
        df = _sf_query(
            "SELECT COUNT(*) AS DUPES FROM ("
            "  SELECT ot.TIMESTAMP, ot.EQUIPMENT_ID, COUNT(*) AS N "
            "  FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_OT_TELEMETRY ot "
            "  LEFT JOIN OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_IT_BATCHES it "
            "    ON ot.EQUIPMENT_ID = it.EQUIPMENT_ID "
            "    AND ot.TIMESTAMP BETWEEN it.START_TIME AND it.END_TIME "
            "  GROUP BY ot.TIMESTAMP, ot.EQUIPMENT_ID "
            "  HAVING COUNT(*) > 1"
            ")"
        )
        assert int(df.iloc[0]["DUPES"]) == 0

    def test_oem_manual_chunks_populated(self):
        df = _sf_query("SELECT COUNT(*) AS CNT FROM OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS")
        assert int(df.iloc[0]["CNT"]) > 0

    def test_ml_forecast_models_exist(self):
        df = _sf_query("SHOW SNOWFLAKE.ML.FORECAST IN SCHEMA OEE_COMMAND_CENTER.FACTORY_FLOOR")
        names = df["name"].tolist()
        assert "EQUIPMENT_TEMP_FORECAST" in names
        assert "EQUIPMENT_VIBE_FORECAST" in names

    def test_webhook_notification_integration_exists(self):
        df = _sf_query("SHOW NOTIFICATION INTEGRATIONS LIKE 'SLACK_WEBHOOK_INT'")
        assert len(df) > 0
