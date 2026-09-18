from typing import Literal, Optional
from pydantic import BaseModel, Field


class WorkingMemory(BaseModel):
    variables: dict = Field(default_factory=dict)
    facts: dict = Field(default_factory=dict)
    flags: dict = Field(default_factory=dict)
    last_error: Optional[dict] = None


class AgentState(BaseModel):
    run_id: str
    step_id: int
    goal: str
    messages: list = Field(default_factory=list)
    context_tokens_estimated: int
    context_budget: int
    token_count_method: str = "tiktoken_cl100k"
    available_tools: list = Field(default_factory=list)
    working_memory: WorkingMemory = Field(default_factory=WorkingMemory)
    pending_action: Optional[dict] = None
    status: Literal["idle", "thinking", "blocked", "recovering", "done"]
