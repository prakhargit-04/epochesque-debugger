from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ErrorInfo(BaseModel):
    type: str
    message: str


class TraceEvent(BaseModel):
    run_id: str
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    parent_event_id: Optional[str] = None
    step_id: int
    turn_id: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: Literal["llm_call", "tool_call", "context_update", "agent_decision", "diagnosis", "recovery"]
    component: str
    status: Literal["success", "failed", "running", "skipped"]
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    error: Optional[ErrorInfo] = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    cost_usd: float = 0.0
    metadata: dict = Field(default_factory=dict)
    truncated: bool = False
