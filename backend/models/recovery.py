from typing import Optional, Literal
from pydantic import BaseModel


class RecoveryAttempt(BaseModel):
    failed_step: int
    diagnosis_event_id: str
    attempt: int
    action: str
    modified_input: dict
    retry_step_id: Optional[int] = None
    result: Literal["pending", "success", "failed"] = "pending"
