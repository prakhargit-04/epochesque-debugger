from typing import Literal, Optional
from pydantic import BaseModel


class Claim(BaseModel):
    text: str
    evidence_event_ids: list[str]


class Diagnosis(BaseModel):
    failure_event_id: str
    tier: Literal["deterministic", "llm"]
    failure_type: str
    root_cause: str
    cause_type: Literal["direct_observation", "supported_inference", "unsupported_speculation"]
    claims: list[Claim]
    confidence: Literal["high", "medium", "low", "insufficient"]
    recovery_action_suggested: Optional[str] = None
    resolved: Optional[bool] = None
