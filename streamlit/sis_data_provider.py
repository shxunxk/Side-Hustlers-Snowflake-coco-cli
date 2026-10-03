"""Session-based data provider for Streamlit in Snowflake."""
import json
import re
import pandas as pd


TELEMETRY_TABLE = "OEE_COMMAND_CENTER.FACTORY_FLOOR.IT_OT_CONVERGED"
BATCH_TABLE = "OEE_COMMAND_CENTER.FACTORY_FLOOR.RAW_IT_BATCHES"
CHUNK_TABLE = "OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_CHUNKS"

STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into",
    "are", "was", "were", "what", "which", "how", "does", "doc",
    "documents", "document", "retrieve", "return", "contents",
    "operating", "manual", "line", "packaging",
}

FALLBACK_OEM_EVIDENCE = {
    "source": "OEM_Maintenance_and_Operations_Manual (Degraded Local Heuristic)",
    "STATUS": "DEGRADED_LOCAL_HEURISTIC",
    "status": "DEGRADED_LOCAL_HEURISTIC",
    "evidence": [
        {"parameter": "temperature", "limit": 90.0, "unit": "C"},
        {"parameter": "vibration", "limit": 2.3, "unit": "mm/s"},
    ],
}


def _query_terms(query):
    terms = re.findall(r"[a-z0-9]+", query.lower())
    return [t for t in terms if len(t) > 2 and t not in STOP_WORDS]


def _score_chunk(text, terms):
    lowered = text.lower()
    matched = sum(1 for t in terms if t in lowered)
    occurrences = sum(lowered.count(t) for t in terms)
    return 2 * matched + occurrences


class SiSDataProvider:
    def __init__(self, session):
        self._session = session

    def _get_df(self, sql):
        return self._session.sql(sql).to_pandas()

    def get_recent_telemetry(self, equipment_id, limit=100):
        sql = f"""
        SELECT
            "TIMESTAMP"AS "Timestamp",
            "EQUIPMENT_ID"AS "Equipment",
            "TEMPERATURE_C"AS "Temperature",
            "VIBRATION_RMS"AS "Vibration"
        FROM {TELEMETRY_TABLE}
        WHERE "EQUIPMENT_ID" = '{equipment_id}'
        ORDER BY "TIMESTAMP"DESC
        LIMIT {limit}
        """
        df = self._get_df(sql)
        if not df.empty and not pd.api.types.is_datetime64_any_dtype(df["Timestamp"]):
            df["Timestamp"] = pd.to_datetime(df["Timestamp"])
        return df

    def get_active_batch(self, equipment_id):
        sql = f"""
        SELECT BATCH_ID, SKU_ID, START_TIME, END_TIME
        FROM {BATCH_TABLE}
        WHERE EQUIPMENT_ID = '{equipment_id}'
        ORDER BY END_TIME DESC
        LIMIT 1
        """
        df = self._get_df(sql)
        if df.empty:
            return {}
        return df.iloc[0].to_dict()

    def search_oem_manual(self, query, limit=5):
        """Search OEM manuals: Cortex Search first, keyword ILIKE fallback."""
        try:
            result = self._cortex_search(query, limit)
            if result is not None:
                return result
        except Exception:
            pass

        return self._keyword_search(query, limit)

    def _cortex_search(self, query, limit=5):
        """Semantic search via Cortex Search Service."""
        safe_query = query.replace("'", "''")
        sql = f"""
            SELECT PARSE_JSON(
                SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
                    'OEE_COMMAND_CENTER.FACTORY_FLOOR.OEM_MANUAL_SEARCH',
                    '{{"query": "{safe_query}", "columns": ["CHUNK_TEXT", "FILE_NAME", "CHUNK_INDEX"], "limit": {limit}}}'
                )
            )['results'] AS results
        """
        df = self._get_df(sql)
        if df.empty or df.iloc[0, 0] is None:
            return None

        raw = df.iloc[0, 0]
        results = json.loads(raw) if isinstance(raw, str) else raw
        if not results:
            return None

        chunks = []
        for r in results:
            chunks.append({
                "file_name": r.get("FILE_NAME", "OEM Manual"),
                "chunk_index": r.get("CHUNK_INDEX", 0),
                "score": 100,
                "text": r.get("CHUNK_TEXT", ""),
            })

        return {
            "source": "CORTEX_SEARCH_SERVICE",
            "query": query,
            "chunks": chunks,
        }

    def _keyword_search(self, query, limit=5):
        """Keyword ILIKE fallback when Cortex Search is unavailable."""
        terms = _query_terms(query)
        if not terms:
            return FALLBACK_OEM_EVIDENCE

        conditions = "OR ".join(
            f"\"CHUNK_TEXT\"ILIKE '%{t}%'" for t in terms
        )
        sql = f"""
        SELECT "FILE_NAME", "CHUNK_INDEX", "CHUNK_TEXT"
        FROM {CHUNK_TABLE}
        WHERE {conditions}
        LIMIT 500
        """
        try:
            df = self._get_df(sql)
            if df.empty:
                return FALLBACK_OEM_EVIDENCE

            rows = df.to_dict("records")
            ranked = sorted(
                (
                    {
                        "file_name": r["FILE_NAME"],
                        "chunk_index": r["CHUNK_INDEX"],
                        "score": _score_chunk(r["CHUNK_TEXT"], terms),
                        "text": r["CHUNK_TEXT"],
                    }
                    for r in rows
                ),
                key=lambda c: c["score"],
                reverse=True,
            )[:limit]

            return {
                "source": "OEM_MANUAL_CHUNKS",
                "query": query,
                "terms": terms,
                "chunks": ranked,
            }
        except Exception:
            return FALLBACK_OEM_EVIDENCE
