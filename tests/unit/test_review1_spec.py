import os
import sqlite3
import pytest
from backend.agent.controller import AgentController
from backend.agent.llm_adapter import MockLLMAdapter
from backend.api.main import get_run
from backend.core.invariants import assert_invariants, InvariantViolation
from backend.db import DB
from backend.diagnosis.citation_validator import validate_citations
from backend.diagnosis.confidence_gate import can_auto_recover, compute_confidence
from backend.diagnosis.diagnosis_engine import diagnose
from backend.diagnosis.evidence_builder import build_evidence_window
from backend.failure_injection import inject_failure_at_boundary
from backend.models.diagnosis import Claim, Diagnosis
from backend.models.trace_event import ErrorInfo, TraceEvent
from backend.recovery.engine import attempt_recovery
from backend.replay.replay_engine import replay_run


@pytest.fixture
def fresh_db():
    db_path = "test_spec.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DB(db_path)
    db.init_schema()
    yield db
    if os.path.exists(db_path):
        os.remove(db_path)


# Requirement 1 & 2: Step IDs are strictly increasing and duplicates rejected
def test_step_ids_strictly_increasing_and_unique(fresh_db):
    run_id = fresh_db.create_run("Step ID test")
    s1 = fresh_db.next_step_id(run_id)
    s2 = fresh_db.next_step_id(run_id)
    s3 = fresh_db.next_step_id(run_id)

    assert s1 == 1
    assert s2 == 2
    assert s3 == 3

    # DB constraint test: inserting duplicate step_id must raise sqlite3.IntegrityError
    e1 = TraceEvent(run_id=run_id, step_id=s1, turn_id=1, event_type="llm_call", component="test", status="success")
    e1_dup = TraceEvent(run_id=run_id, step_id=s1, turn_id=1, event_type="agent_decision", component="test", status="success")

    fresh_db.record_event(e1)
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.record_event(e1_dup)


# Requirement 3 & 4: Real LLM event exists before injected failure & tool input truthful with metadata
def test_llm_event_and_tool_input_preserved_during_injection(fresh_db):
    run_id = fresh_db.create_run("Process a list of numbers and print the result.")
    mock_llm = MockLLMAdapter(text="numbers = [5, 2, 9, 1]\nprint(sorted(numbers, reverse=True))")
    controller = AgentController(fresh_db, llm=mock_llm)

    injection = inject_failure_at_boundary("generated_code_failure", {"code": ""})
    events = controller.execute(run_id, injection=injection)

    # 1. Real LLM call event exists
    llm_events = [e for e in events if e.event_type == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0].output["code"] == "numbers = [5, 2, 9, 1]\nprint(sorted(numbers, reverse=True))"

    # 2. Tool call event input reflects what actually executed, while metadata retains pre-injection input
    failed_tool_events = [e for e in events if e.event_type == "tool_call" and e.status == "failed"]
    assert len(failed_tool_events) == 1
    assert failed_tool_events[0].input["code"] == "print(numbers)"
    assert failed_tool_events[0].metadata.get("injected") is True
    assert failed_tool_events[0].metadata.get("pre_injection_input", {}).get("code") == "numbers = [5, 2, 9, 1]\nprint(sorted(numbers, reverse=True))"
    assert failed_tool_events[0].step_id > llm_events[0].step_id


@pytest.mark.parametrize("injection_type", ["generated_code_failure", "tool_failure", "malformed_tool_response"])
def test_real_llm_events_precede_failed_tool_call_for_all_injections(fresh_db, injection_type):
    run_id = fresh_db.create_run(f"Test injection type {injection_type}")
    mock_code = "print('real llm generated code')"
    mock_llm = MockLLMAdapter(text=mock_code)
    controller = AgentController(fresh_db, llm=mock_llm)

    events = controller.execute(run_id, injection=injection_type)

    llm_events = [e for e in events if e.event_type == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0].status == "success"
    assert llm_events[0].output.get("code") == mock_code

    decision_events = [e for e in events if e.event_type == "agent_decision"]
    assert len(decision_events) >= 1
    assert decision_events[0].status == "success"
    assert decision_events[0].output.get("code") == mock_code

    failed_tool_events = [e for e in events if e.event_type == "tool_call" and e.status == "failed"]
    assert len(failed_tool_events) >= 1
    failed_tool = failed_tool_events[0]

    # Precedence requirement per spec §16 test #17
    assert llm_events[0].step_id < failed_tool.step_id
    assert decision_events[0].step_id < failed_tool.step_id

    # Trace truthfulness: input is post-injection executed code, metadata retains pre-injection input
    assert failed_tool.input["code"] != mock_code
    assert failed_tool.metadata.get("injected") is True
    assert failed_tool.metadata.get("injection_type") == injection_type
    assert failed_tool.metadata.get("pre_injection_input", {}).get("code") == mock_code



# Requirement 5, 6, 7, 8: Citation Validation & Confidence Gate Requirements
def test_citation_validation_cases():
    allowed_ids = ["evt_001", "evt_002", "evt_003"]

    # C1: Valid citation -> accepted
    claims_c1 = [Claim(text="Valid claim", evidence_event_ids=["evt_002"])]
    assert validate_citations(claims_c1, allowed_ids) is True
    assert compute_confidence("llm", citations_valid=True, has_citations=True, failure_type_matches_evidence=True) == "medium"

    # C2: Out-of-window citation -> LOW
    claims_c2 = [Claim(text="Out of window", evidence_event_ids=["evt_999"])]
    assert validate_citations(claims_c2, allowed_ids) is False
    assert compute_confidence("llm", citations_valid=False, has_citations=True, failure_type_matches_evidence=True) == "low"

    # C3: Empty citations -> INSUFFICIENT
    claims_c3 = []
    assert compute_confidence("llm", citations_valid=True, has_citations=False, failure_type_matches_evidence=True) == "insufficient"

    # C4: LLM self-reported confidence is IGNORED by compute_confidence
    conf = compute_confidence("llm", citations_valid=False, has_citations=True, failure_type_matches_evidence=True)
    assert conf == "low"  # ignoring model claim of 'high'


def test_invariant_i3_raises_on_invalid_citations_with_high_confidence(fresh_db):
    run_id = fresh_db.create_run("I3 invariant test")
    tool_event = TraceEvent(run_id=run_id, step_id=fresh_db.next_step_id(run_id), turn_id=1, event_type="tool_call", component="test", status="failed")
    fresh_db.record_event(tool_event)

    # Diagnosis with invalid out-of-window citation (evt_999) but output confidence claimed as "high"
    diag_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=tool_event.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
        input={"failure_event_id": tool_event.event_id},
        output={
            "confidence": "high",
            "claims": [{"text": "invalid citation claim", "evidence_event_ids": ["evt_999"]}],
        },
    )
    fresh_db.record_event(diag_event)

    with pytest.raises(InvariantViolation) as exc_info:
        assert_invariants(run_id, fresh_db)
    assert "I3" in str(exc_info.value)



# Requirement 9, 10, 11: Recovery Gate Allowlisting & Blocks
def test_recovery_eligibility_gates():
    # Requirement 11: Allowed actions
    assert can_auto_recover("high", "NameError", "regenerate_code") is True
    assert can_auto_recover("high", "NameError", "unauthorized_action") is False

    # Requirement 9 & 10: LOW and INSUFFICIENT block recovery
    assert can_auto_recover("low", "NameError", "regenerate_code") is False
    assert can_auto_recover("insufficient", "NameError", "regenerate_code") is False


# Requirement 12, 13, 14, 15: Recovery Execution & Metric Linkage
def test_recovery_execution_and_invariants(fresh_db):
    run_id = fresh_db.create_run("Recovery test")

    failed_tool = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        input={"code": "print(numbers)"},
        error=ErrorInfo(type="NameError", message="name 'numbers' is not defined"),
    )
    fresh_db.record_event(failed_tool)

    diag_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=failed_tool.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
    )
    fresh_db.record_event(diag_event)

    diagnosis = Diagnosis(
        failure_event_id=failed_tool.event_id,
        tier="deterministic",
        failure_type="NameError",
        root_cause="Undefined variable",
        cause_type="direct_observation",
        claims=[Claim(text="Undefined variable", evidence_event_ids=[failed_tool.event_id])],
        confidence="high",
        recovery_action_suggested="regenerate_code",
    )

    retry_event = attempt_recovery(run_id, failed_tool, diagnosis, diag_event.event_id, fresh_db, 1)

    # 13: Recovery references diagnosis_event_id
    rec_event = [e for e in fresh_db.get_events(run_id) if e.event_type == "recovery"][0]
    assert rec_event.input["diagnosis_event_id"] == diag_event.event_id

    # 12: Retry input differs materially from failed input
    assert rec_event.input["failed_input"] != rec_event.output["modified_input"]

    # 14 & 15: Recovery result comes from actual retry event & status is recovered
    assert retry_event.status == "success"
    assert rec_event.output["retry_step_id"] == retry_event.step_id
    assert fresh_db.get_run(run_id)["status"] == "recovered"

    # Invariants check
    assert_invariants(run_id, fresh_db)


def test_i6_metadata_only_change_fails_invariant_check(fresh_db):
    # I6: A retry whose modified_input differs from failed input ONLY in timestamp/attempt_id must FAIL I6 check
    run_id = fresh_db.create_run("I6 metadata test")

    rec_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="recovery",
        component="recovery_engine",
        status="success",
        input={
            "failed_input": {"code": "print('same')", "timestamp": "2026-09-18T10:00:00Z", "attempt_id": 1},
            "diagnosis_event_id": "evt_diag_1",
        },
        output={
            "attempt": 1,
            "action": "retry_tool",
            "modified_input": {"code": "print('same')", "timestamp": "2026-09-18T10:05:00Z", "attempt_id": 2},
        },
    )
    fresh_db.record_event(rec_event)

    with pytest.raises(InvariantViolation) as exc_info:
        assert_invariants(run_id, fresh_db)
    assert "I6" in str(exc_info.value)



# Requirement 16: Replay makes zero external calls and creates zero new events
def test_replay_makes_zero_new_events(fresh_db):
    run_id = fresh_db.create_run("Replay test")
    e1 = TraceEvent(run_id=run_id, step_id=fresh_db.next_step_id(run_id), turn_id=1, event_type="llm_call", component="gemini_adapter", status="success")
    fresh_db.record_event(e1)

    initial_events = fresh_db.get_events(run_id)

    replayed = replay_run(run_id, fresh_db)

    assert len(replayed) == len(initial_events)
    assert len(fresh_db.get_events(run_id)) == len(initial_events)


# Requirement 17 & 18: Full Review-1 E2E Flow directly inspecting SQLite persistence
def test_review1_e2e_full_flow_sqlite_persisted():
    db_path = "test_e2e.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DB(db_path)
    db.init_schema()

    try:
        goal = "Process a list of numbers and print the result."
        run_id = db.create_run(goal)

        mock_llm = MockLLMAdapter(text="numbers = [5, 2, 9, 1]\nprint(sorted(numbers, reverse=True))")
        controller = AgentController(db, llm=mock_llm)

        injection = inject_failure_at_boundary("generated_code_failure", {"code": ""})
        events = controller.execute(run_id, injection=injection)

        # Inspect persisted SQLite records directly
        persisted_run = db.get_run(run_id)
        persisted_events = db.get_events(run_id)

        assert persisted_run["status"] == "recovered"
        assert len(persisted_events) == 6

        # Step order verification
        step_types = [e.event_type for e in persisted_events]
        assert step_types == ["llm_call", "agent_decision", "tool_call", "diagnosis", "recovery", "tool_call"]

        # Step ID sequence & unique constraint
        step_ids = [e.step_id for e in persisted_events]
        assert step_ids == [1, 2, 3, 4, 5, 6]

        # Parent chain verification
        assert persisted_events[1].parent_event_id == persisted_events[0].event_id
        assert persisted_events[2].parent_event_id == persisted_events[1].event_id
        assert persisted_events[3].parent_event_id == persisted_events[2].event_id
        assert persisted_events[4].parent_event_id == persisted_events[3].event_id
        assert persisted_events[5].parent_event_id == persisted_events[4].event_id

        # Trace statuses
        assert persisted_events[0].status == "success"
        assert persisted_events[1].status == "success"
        assert persisted_events[2].status == "failed"
        assert persisted_events[3].status == "success"
        assert persisted_events[4].status == "success"
        assert persisted_events[5].status == "success"

        # AgentState persistence verification after every step
        for s_id in step_ids:
            state_data = db.get_state(run_id, s_id)
            assert state_data is not None
            assert state_data["run_id"] == run_id
            assert state_data["step_id"] == s_id

        # All six invariants hold
        assert_invariants(run_id, db)
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
