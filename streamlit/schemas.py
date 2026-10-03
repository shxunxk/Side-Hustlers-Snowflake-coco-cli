"""Pydantic v2 schemas  identical to code/execute_detection/schemas.py."""
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class DiagnosticState(BaseModel):
    equipment_id: str
    metric: str
    current_val: float
    dynamic_threshold: float
    rul_hours: float
    failure_flag: bool
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    model_config = ConfigDict(extra="ignore")


class OEMValidation(BaseModel):
    max_temp_limit: float
    max_vibration_limit: float
    citation_source: str
    breach_detected: bool
    status: Optional[str] = None
    model_config = ConfigDict(extra="ignore")


class MitigationDecision(BaseModel):
    priority: Literal["CRITICAL", "HIGH", "MEDIUM"]
    action: Literal["IMMEDIATE_MAINTENANCE", "SCHEDULE_MAINTENANCE", "MONITOR_EQUIPMENT"]
    target_sku: Optional[str] = None
    justification: str
    approved_for_dispatch: bool
    model_config = ConfigDict(extra="forbid")
