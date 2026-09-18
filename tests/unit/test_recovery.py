from backend.agent.controller import AgentController
from backend.agent.llm_adapter import MockLLMAdapter
from backend.db import DB
from backend.models.diagnosis import Claim, Diagnosis
from backend.models.trace_event import ErrorInfo, TraceEvent
from backend.recovery.engine import attempt_recovery, is_recovery_resolved


def test_attempt_recovery_populates_retry_step_id():
    db = DB()
    db.init_schema()
    run_id = db.create_run("Test recovery retry_step_id")

    failure_event = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        input={"code": "print(numbers)"},
        error=ErrorInfo(type="NameError", message="name 'undefined_var' is not defined"),
    )
    db.record_event(failure_event)

    diagnosis_event = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=failure_event.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
    )
    db.record_event(diagnosis_event)

    diagnosis = Diagnosis(
        failure_event_id=failure_event.event_id,
        tier="deterministic",
        failure_type="NameError",
        root_cause="Undefined variable referenced in generated code.",
        cause_type="direct_observation",
        claims=[Claim(text="Undefined variable", evidence_event_ids=[failure_event.event_id])],
        confidence="high",
        recovery_action_suggested="regenerate_code",
    )

    retry_event = attempt_recovery(run_id, failure_event, diagnosis, diagnosis_event.event_id, db, 1)

    assert retry_event is not None
    assert retry_event.status == "success"

    events = db.get_events(run_id)
    recovery_events = [e for e in events if e.event_type == "recovery"]
    assert len(recovery_events) == 1

    rec = recovery_events[0]
    retry_step_id = rec.output.get("retry_step_id")
    assert retry_step_id is not None
    assert retry_step_id == retry_event.step_id

    # Check that metric calculation in GET /runs/{id} evaluates success rate correctly
    from backend.api.main import get_run
    run_info = get_run(run_id)
    assert run_info["recovery_success_rate"] == 1.0


# Test #5: Recovery attempts exhausting at 2 -> run.status = "blocked"
def test_recovery_attempts_exhausting_at_2_blocks_run():
    db = DB()
    db.init_schema()
    run_id = db.create_run("Test recovery exhaustion at 2")

    # Construct failing failure event with code that will fail execution on attempt 1 and 2
    failing_code = "raise ConnectionError('persistent failure')"
    failed_tool = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        input={"code": failing_code},
        error=ErrorInfo(type="ConnectionError", message="persistent failure"),
    )
    db.record_event(failed_tool)

    diag_event_1 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=failed_tool.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
    )
    db.record_event(diag_event_1)

    diagnosis_1 = Diagnosis(
        failure_event_id=failed_tool.event_id,
        tier="deterministic",
        failure_type="NameError",  # Will attempt regenerate_code which still fails
        root_cause="Undefined variable",
        cause_type="direct_observation",
        claims=[Claim(text="Undefined variable", evidence_event_ids=[failed_tool.event_id])],
        confidence="high",
        recovery_action_suggested="regenerate_code",
    )

    # Attempt 1 -> fails
    retry_1 = attempt_recovery(run_id, failed_tool, diagnosis_1, diag_event_1.event_id, db, attempt_number=1)
    assert retry_1 is not None

    # Attempt 2 -> fails and exhausts retries
    retry_2 = attempt_recovery(run_id, retry_1, diagnosis_1, diag_event_1.event_id, db, attempt_number=2)
    assert retry_2 is not None

    # Run status MUST be blocked after attempt 2 exhaustion
    run_info = db.get_run(run_id)
    assert run_info["status"] == "blocked"

    # Attempting a 3rd recovery MUST fail the I5 assertion
    import pytest
    with pytest.raises(AssertionError) as exc_info:
        attempt_recovery(run_id, retry_2, diagnosis_1, diag_event_1.event_id, db, attempt_number=3)
    assert "I5" in str(exc_info.value)


# Test #6: A failed retry triggers a genuinely NEW diagnosis object (not a reused one)
def test_failed_retry_triggers_fresh_new_diagnosis_object():
    db = DB()
    db.init_schema()
    run_id = db.create_run("Test fresh diagnosis on failed retry")

    # Mock LLM returns code that fails with NameError
    mock_llm = MockLLMAdapter(text="print(undefined_var_x)")
    controller = AgentController(db, llm=mock_llm)

    events = controller.execute(run_id)

    # Filter diagnosis events and recovery events
    diag_events = [e for e in events if e.event_type == "diagnosis"]
    rec_events = [e for e in events if e.event_type == "recovery"]

    # There must be 2 distinct diagnosis events
    assert len(diag_events) == 2
    assert len(rec_events) == 2

    # Assert that the second diagnosis event is a genuinely NEW object with a distinct event_id
    assert diag_events[0].event_id != diag_events[1].event_id
    assert diag_events[0].step_id < diag_events[1].step_id

    # Assert that the second recovery event references the SECOND diagnosis event, NOT the first
    assert rec_events[0].input["diagnosis_event_id"] == diag_events[0].event_id
    assert rec_events[1].input["diagnosis_event_id"] == diag_events[1].event_id


def test_is_recovery_resolved_evaluates_linked_retry_step():
    db = DB()
    db.init_schema()
    run_id = db.create_run("is_recovery_resolved test")

    # Step 1: recovery event pointing to step 2
    rec_event = TraceEvent(
        run_id=run_id,
        step_id=1,
        turn_id=1,
        event_type="recovery",
        component="recovery_engine",
        status="success",
        output={"retry_step_id": 2},
    )

    # Before retry event exists, is_recovery_resolved is None
    assert is_recovery_resolved(rec_event, db) is None

    # Step 2: successful tool_call retry event
    tool_success = TraceEvent(
        run_id=run_id,
        step_id=2,
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="success",
    )
    db.record_event(tool_success)

    # is_recovery_resolved must derive True from tool_success status
    assert is_recovery_resolved(rec_event, db) is True

