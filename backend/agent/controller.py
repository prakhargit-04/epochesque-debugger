import time

from backend.agent.llm_adapter import GeminiAdapter
from backend.config import CONTEXT_BUDGET, DEFAULT_MAX_TOKENS, LLM_MODEL, MAX_LLM_RETRIES
from backend.context.context_manager import estimate_tokens, manage_context
from backend.core.invariants import assert_invariants
from backend.db import DB
from backend.diagnosis.diagnosis_engine import diagnose
from backend.executor.sandbox import run_code
from backend.failure_injection import inject_failure_at_boundary
from backend.models.agent_state import AgentState, WorkingMemory
from backend.models.trace_event import ErrorInfo, TraceEvent, Usage
from backend.recovery.engine import attempt_recovery
from backend.services.pricing import calculate_cost

SYSTEM_PROMPT = "You are an execution agent. Return only short Python code that solves the user's goal."


class AgentController:
    def __init__(self, db: DB, llm=None):
        self.db = db
        if llm is None:
            from backend.config import LLM_PROVIDER
            if LLM_PROVIDER == "mock":
                from backend.agent.llm_adapter import MockLLMAdapter
                self.llm = MockLLMAdapter()
            else:
                self.llm = GeminiAdapter()
        else:
            self.llm = llm

    def _save_state(
        self,
        run_id: str,
        step_id: int,
        goal: str,
        messages: list,
        status: str,
        pending_action: dict | None = None,
        last_error: dict | None = None,
    ):
        state = AgentState(
            run_id=run_id,
            step_id=step_id,
            goal=goal,
            messages=messages,
            context_tokens_estimated=estimate_tokens(messages),
            context_budget=CONTEXT_BUDGET,
            status=status,
            working_memory=WorkingMemory(last_error=last_error),
            pending_action=pending_action,
        )
        self.db.save_state(run_id, step_id, state.model_dump(mode="json"))

    def execute(self, run_id: str, injection=None):
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("run not found")
        if not self.db.try_lock_run(run_id):
            raise RuntimeError("RUN_BUSY")
        try:
            turn_id = max((e.turn_id for e in self.db.get_events(run_id)), default=0) + 1
            parent = None
            goal = run["goal"]
            messages = [{"role": "user", "content": goal}]
            context = manage_context(messages, goal)
            if context["action"] == "blocked":
                self.db.update_run_status(run_id, "blocked", ended=True)
                return self.db.get_events(run_id)

            if context["action"] != "none":
                context_event = TraceEvent(
                    run_id=run_id,
                    step_id=self.db.next_step_id(run_id),
                    turn_id=turn_id,
                    parent_event_id=parent,
                    event_type="context_update",
                    component="context_manager",
                    status="success",
                    input={"context_tokens_estimated_before": context["tokens"]},
                    output={"action": context["action"], "context_tokens_estimated_after": context["tokens"]},
                )
                self.db.record_event(context_event)
                self._save_state(run_id, context_event.step_id, goal, messages, "thinking")
                parent = context_event.event_id

            started = time.time()
            response = None
            llm_error = None
            for attempt in range(1 + MAX_LLM_RETRIES):
                try:
                    response = self.llm.call(context["messages"], system=SYSTEM_PROMPT, max_tokens=DEFAULT_MAX_TOKENS)
                    if response and "text" in response:
                        break
                except Exception as exc:
                    llm_error = str(exc)

            latency = int((time.time() - started) * 1000)
            if not response or "text" not in response or not response["text"].strip():
                failed_llm_event = TraceEvent(
                    run_id=run_id,
                    step_id=self.db.next_step_id(run_id),
                    turn_id=turn_id,
                    parent_event_id=parent,
                    event_type="llm_call",
                    component="gemini_adapter",
                    status="failed",
                    input={"goal": goal, "model": LLM_MODEL},
                    output={},
                    latency_ms=latency,
                    error=ErrorInfo(type="LLMProviderError", message=llm_error or "LLM response empty"),
                )
                self.db.record_event(failed_llm_event)
                self._save_state(run_id, failed_llm_event.step_id, goal, messages, "blocked", last_error=failed_llm_event.error.model_dump() if failed_llm_event.error else None)
                self.db.update_run_status(run_id, "failed", ended=True)
                return self.db.get_events(run_id)

            usage = Usage(**response.get("usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}))
            llm_event = TraceEvent(
                run_id=run_id,
                step_id=self.db.next_step_id(run_id),
                turn_id=turn_id,
                parent_event_id=parent,
                event_type="llm_call",
                component="gemini_adapter",
                status="success",
                input={"goal": goal, "model": LLM_MODEL},
                output={"code": response["text"]},
                usage=usage,
                latency_ms=latency,
                cost_usd=calculate_cost(LLM_MODEL, usage.input_tokens, usage.output_tokens),
            )
            self.db.record_event(llm_event)
            self._save_state(run_id, llm_event.step_id, goal, messages, "thinking")
            parent = llm_event.event_id

            decision_event = TraceEvent(
                run_id=run_id,
                step_id=self.db.next_step_id(run_id),
                turn_id=turn_id,
                parent_event_id=parent,
                event_type="agent_decision",
                component="agent_controller",
                status="success",
                input={"llm_event_id": llm_event.event_id},
                output={"decision": "execute_generated_code", "code": response["text"]},
            )
            self.db.record_event(decision_event)
            self._save_state(run_id, decision_event.step_id, goal, messages, "thinking", pending_action={"code": response["text"]})
            parent = decision_event.event_id

            real_tool_input = {"code": response["text"]}
            tool_input = real_tool_input
            metadata = {}
            if injection:
                if isinstance(injection, dict):
                    inj_type = injection.get("injection_type", "generated_code_failure")
                    code_to_run = injection.get("code", response["text"])
                    tool_input = {"code": code_to_run}
                    metadata = {
                        "injected": True,
                        "injection_type": inj_type,
                        "pre_injection_input": real_tool_input,
                    }
                elif isinstance(injection, str):
                    inj_type = injection
                    mutated = inject_failure_at_boundary(inj_type, real_tool_input)
                    tool_input = {"code": mutated["code"]}
                    metadata = {
                        "injected": True,
                        "injection_type": inj_type,
                        "pre_injection_input": real_tool_input,
                    }

            code_to_run = tool_input["code"]
            result = run_code(code_to_run)
            tool = TraceEvent(
                run_id=run_id,
                step_id=self.db.next_step_id(run_id),
                turn_id=turn_id,
                parent_event_id=parent,
                event_type="tool_call",
                component="constrained_executor",
                status="success" if result["success"] else "failed",
                input=tool_input,
                output=result.get("output", {}),
                latency_ms=result.get("latency_ms", 0),
                metadata=metadata,
            )
            if not result["success"]:
                tool.error = ErrorInfo(type=result.get("error_type", "RuntimeError"), message=result.get("error_message", "unknown"))
            self.db.record_event(tool)
            self._save_state(run_id, tool.step_id, goal, messages, "recovering" if tool.status == "failed" else "done", last_error=tool.error.model_dump() if tool.error else None)

            if tool.status == "failed":
                events = self.db.get_events(run_id)
                diagnosis = diagnose(tool, events, self.llm)
                diagnosis_event = TraceEvent(
                    run_id=run_id,
                    step_id=self.db.next_step_id(run_id),
                    turn_id=turn_id,
                    parent_event_id=tool.event_id,
                    event_type="diagnosis",
                    component="diagnosis_engine",
                    status="success",
                    input={"failure_event_id": tool.event_id},
                    output=diagnosis.model_dump(),
                )
                self.db.record_event(diagnosis_event)
                self._save_state(run_id, diagnosis_event.step_id, goal, messages, "recovering")
                retry = attempt_recovery(run_id, tool, diagnosis, diagnosis_event.event_id, self.db, 1)
                # A failed retry is a new execution fact. Diagnose it afresh;
                # never reuse the earlier diagnosis for a second attempt.
                if retry and retry.status == "failed":
                    retry_diagnosis = diagnose(retry, self.db.get_events(run_id), self.llm)
                    retry_diagnosis_event = TraceEvent(
                        run_id=run_id,
                        step_id=self.db.next_step_id(run_id),
                        turn_id=turn_id,
                        parent_event_id=retry.event_id,
                        event_type="diagnosis",
                        component="diagnosis_engine",
                        status="success",
                        input={"failure_event_id": retry.event_id},
                        output=retry_diagnosis.model_dump(),
                    )
                    self.db.record_event(retry_diagnosis_event)
                    self._save_state(run_id, retry_diagnosis_event.step_id, goal, messages, "recovering")
                    attempt_recovery(run_id, retry, retry_diagnosis, retry_diagnosis_event.event_id, self.db, 2)
            else:
                self.db.update_run_status(run_id, "completed", ended=True)
            assert_invariants(run_id, self.db)
            return self.db.get_events(run_id)
        finally:
            current = self.db.get_run(run_id)
            if current and current["status"] == "busy":
                self.db.update_run_status(run_id, "running")
