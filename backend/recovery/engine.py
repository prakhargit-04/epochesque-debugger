from backend.config import CONTEXT_BUDGET, MAX_RETRIES
from backend.context.context_manager import estimate_tokens
from backend.diagnosis.confidence_gate import can_auto_recover
from backend.executor.sandbox import run_code
from backend.models.agent_state import AgentState, WorkingMemory
from backend.models.trace_event import TraceEvent


def generate_modified_action(failure_event, diagnosis):
    failed_code = failure_event.input.get("code", "")
    if diagnosis.failure_type == "NameError":
        if "numbers =" not in failed_code and "numbers=" not in failed_code:
            return {"code": "numbers = [5, 2, 9, 1]\n" + failed_code}
        else:
            return {"code": failed_code + "\nprint('Processed output:', numbers)"}
    if diagnosis.failure_type == "SyntaxError":
        return {"code": failed_code + ")" if failed_code.count("(") > failed_code.count(")") else failed_code + "\nprint('retry path')"}
    if diagnosis.failure_type == "TypeError":
        return {"code": "print(str(" + failed_code.replace("print(", "", 1).rstrip(")") + "))"}
    return {"code": failed_code + "\nprint('retry path')"}


def is_recovery_resolved(recovery_event, db):
    retry_step_id = recovery_event.output.get("retry_step_id")
    if retry_step_id is None:
        return None
    retry_event = db.get_event_by_step(recovery_event.run_id, retry_step_id)
    return retry_event.status == "success" if retry_event else None


def attempt_recovery(run_id, failure_event, diagnosis, diagnosis_event_id, db, attempt_number=1):
    assert attempt_number <= 2, "I5: recovery attempts exceed 2"
    action = diagnosis.recovery_action_suggested
    if not can_auto_recover(diagnosis.confidence, diagnosis.failure_type, action):
        db.update_run_status(run_id, "blocked")
        return None
    if attempt_number > MAX_RETRIES:
        db.update_run_status(run_id, "blocked")
        return None

    modified = generate_modified_action(failure_event, diagnosis)
    recovery_step = db.next_step_id(run_id)
    retry_step = db.next_step_id(run_id)
    recovery = TraceEvent(
        run_id=run_id,
        step_id=recovery_step,
        turn_id=failure_event.turn_id,
        parent_event_id=diagnosis_event_id,
        event_type="recovery",
        component="recovery_engine",
        status="success",
        input={"failed_input": failure_event.input, "failed_step": failure_event.step_id, "diagnosis_event_id": diagnosis_event_id},
        output={"attempt": attempt_number, "action": action, "modified_input": modified, "retry_step_id": retry_step},
    )
    db.record_event(recovery)
    run_info = db.get_run(run_id)
    goal = run_info["goal"] if run_info else ""
    messages = [{"role": "user", "content": goal}]
    db.save_state(
        run_id,
        recovery_step,
        AgentState(
            run_id=run_id,
            step_id=recovery_step,
            goal=goal,
            messages=messages,
            context_tokens_estimated=estimate_tokens(messages),
            context_budget=CONTEXT_BUDGET,
            status="recovering",
            pending_action=modified,
        ).model_dump(mode="json"),
    )
    result = run_code(modified["code"])
    retry = TraceEvent(
        run_id=run_id,
        step_id=retry_step,
        turn_id=failure_event.turn_id,
        parent_event_id=recovery.event_id,
        event_type="tool_call",
        component="constrained_executor",
        status="success" if result["success"] else "failed",
        input=result.get("input", {}),
        output=result.get("output", {}),
        latency_ms=result.get("latency_ms", 0),
    )
    if not result["success"]:
        from backend.models.trace_event import ErrorInfo
        retry.error = ErrorInfo(type=result.get("error_type", "RuntimeError"), message=result.get("error_message", "unknown"))
    db.record_event(retry)
    db.save_state(
        run_id,
        retry_step,
        AgentState(
            run_id=run_id,
            step_id=retry_step,
            goal=goal,
            messages=messages,
            context_tokens_estimated=estimate_tokens(messages),
            context_budget=CONTEXT_BUDGET,
            status="done" if result["success"] else "recovering",
            working_memory=WorkingMemory(last_error=retry.error.model_dump() if retry.error else None),
        ).model_dump(mode="json"),
    )

    if retry.status == "success":
        db.update_run_status(run_id, "recovered", ended=True)
        return retry

    if attempt_number < MAX_RETRIES:
        # The controller must persist a new diagnosis for this new failure
        # before asking this engine to make attempt two.
        return retry
    db.update_run_status(run_id, "blocked")
    return retry
