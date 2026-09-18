import os
import sqlite3
import time
import pytest
from unittest.mock import patch, MagicMock

from backend.agent.controller import AgentController
from backend.agent.llm_adapter import MockLLMAdapter
from backend.config import (
    CONTEXT_BUDGET,
    EVIDENCE_WINDOW_SIZE,
    INJECTION_TYPES,
    ALLOWED_RECOVERY_ACTIONS,
)
from backend.context.context_manager import manage_context
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
    db_path = "test_phase8.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DB(db_path)
    db.init_schema()
    yield db
    if os.path.exists(db_path):
        os.remove(db_path)


# 1. Fake citation (event_id that doesn't exist anywhere in the DB)
def test_fake_citation(fresh_db):
    run_id = fresh_db.create_run("Fake citation attack test")
    tool_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        error=ErrorInfo(type="RuntimeError", message="Test failure"),
    )
    fresh_db.record_event(tool_event)

    allowed_ids = build_evidence_window(fresh_db.get_events(run_id), tool_event)
    claims = [Claim(text="Fake evidence claim", evidence_event_ids=["evt_fake_99999"])]
    is_valid = validate_citations(claims, allowed_ids)
    assert is_valid is False

    conf = compute_confidence("llm", citations_valid=is_valid, has_citations=True, failure_type_matches_evidence=True)
    assert conf == "low"


# 2. Cross-run citation (real event_id, but from a DIFFERENT run_id)
def test_cross_run_citation(fresh_db):
    run_1 = fresh_db.create_run("Run 1")
    e_other = TraceEvent(
        run_id=run_1,
        step_id=fresh_db.next_step_id(run_1),
        turn_id=1,
        event_type="tool_call",
        component="test",
        status="failed",
    )
    fresh_db.record_event(e_other)

    run_2 = fresh_db.create_run("Run 2 - Target")
    e_target = TraceEvent(
        run_id=run_2,
        step_id=fresh_db.next_step_id(run_2),
        turn_id=1,
        event_type="tool_call",
        component="test",
        status="failed",
        error=ErrorInfo(type="RuntimeError", message="Run 2 error"),
    )
    fresh_db.record_event(e_target)

    allowed_ids_run2 = build_evidence_window(fresh_db.get_events(run_2), e_target)
    assert e_other.event_id not in set(allowed_ids_run2)

    claims = [Claim(text="Cross run citation claim", evidence_event_ids=[e_other.event_id])]
    is_valid = validate_citations(claims, allowed_ids_run2)
    assert is_valid is False

    conf = compute_confidence("llm", citations_valid=is_valid, has_citations=True, failure_type_matches_evidence=True)
    assert conf == "low"


# 3. Out-of-window citation (real event in this run, but outside frozen evidence window -> C2 / "low", not "insufficient")
def test_out_of_window_citation(fresh_db):
    run_id = fresh_db.create_run("Out of window test")
    recorded = []
    for i in range(10):
        evt = TraceEvent(
            run_id=run_id,
            step_id=fresh_db.next_step_id(run_id),
            turn_id=1,
            event_type="tool_call",
            component="test",
            status="failed",
        )
        fresh_db.record_event(evt)
        recorded.append(evt)

    failed_evt = recorded[-1]
    allowed_ids = build_evidence_window(fresh_db.get_events(run_id), failed_evt)
    
    old_evt_id = recorded[0].event_id
    assert old_evt_id not in set(allowed_ids)

    claims = [Claim(text="Out of window citation", evidence_event_ids=[old_evt_id])]
    is_valid = validate_citations(claims, allowed_ids)
    assert is_valid is False

    conf = compute_confidence("llm", citations_valid=is_valid, has_citations=True, failure_type_matches_evidence=True)
    assert conf == "low"


# 4. Empty citation (C3 -> "insufficient")
def test_empty_citation():
    conf = compute_confidence("llm", citations_valid=True, has_citations=False, failure_type_matches_evidence=True)
    assert conf == "insufficient"


# 5. LLM claims HIGH confidence in raw JSON (C4: application overrides it to "low" and invariants pass)
def test_llm_claims_high_confidence_overridden(fresh_db):
    run_id = fresh_db.create_run("LLM confidence override test")
    failed_tool = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        error=ErrorInfo(type="ZeroDivisionError", message="division by zero"),
    )
    fresh_db.record_event(failed_tool)

    llm_payload = '{"failure_type": "ZeroDivisionError", "root_cause": "Divided by zero", "cause_type": "direct_observation", "claims": [{"text": "Zero division", "evidence_event_ids": ["evt_fake_123"]}], "confidence": "high", "recovery_action_suggested": "regenerate_code"}'
    mock_llm = MockLLMAdapter(text=llm_payload)

    events = fresh_db.get_events(run_id)
    diag = diagnose(failed_tool, events, mock_llm)

    assert diag.confidence == "low"

    diag_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=failed_tool.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
        input={"failure_event_id": failed_tool.event_id},
        output=diag.model_dump(),
    )
    fresh_db.record_event(diag_event)

    # System correctly overrode LLM claim, so assert_invariants does NOT raise
    assert_invariants(run_id, fresh_db)


# 6. Invariant I3 backstop test for corrupted diagnosis event
def test_invariant_i3_catches_corrupted_diagnosis(fresh_db):
    run_id = fresh_db.create_run("I3 corrupted diagnosis test")
    failed_tool = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="test",
        status="failed",
    )
    fresh_db.record_event(failed_tool)

    corrupted_diag_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=failed_tool.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
        input={"failure_event_id": failed_tool.event_id},
        output={
            "confidence": "high",
            "claims": [{"text": "corrupted claim", "evidence_event_ids": ["evt_fake_nonexistent"]}],
        },
    )
    fresh_db.record_event(corrupted_diag_event)

    with pytest.raises(InvariantViolation) as exc_info:
        assert_invariants(run_id, fresh_db)
    assert "I3" in str(exc_info.value)


# 7. Recovery action manipulation (diagnosis suggests action outside ALLOWED_RECOVERY_ACTIONS)
def test_recovery_action_manipulation_rejected(fresh_db):
    assert "unauthorized_action" not in ALLOWED_RECOVERY_ACTIONS
    assert "delete_database" not in ALLOWED_RECOVERY_ACTIONS

    assert can_auto_recover("high", "NameError", "unauthorized_action") is False
    assert can_auto_recover("high", "NameError", "delete_database") is False

    run_id = fresh_db.create_run("Recovery manipulation test")
    failed_tool = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        error=ErrorInfo(type="NameError", message="name 'x' is not defined"),
    )
    fresh_db.record_event(failed_tool)

    diagnosis = Diagnosis(
        failure_event_id=failed_tool.event_id,
        tier="deterministic",
        failure_type="NameError",
        root_cause="Undefined variable",
        cause_type="direct_observation",
        claims=[Claim(text="Undefined variable", evidence_event_ids=[failed_tool.event_id])],
        confidence="high",
        recovery_action_suggested="delete_database",
    )

    retry_event = attempt_recovery(run_id, failed_tool, diagnosis, "evt_diag_01", fresh_db, 1)
    assert retry_event is None


# 8. Duplicate execution request (two /execute calls fired on same run -> 409 RUN_BUSY)
def test_duplicate_execution_request_busy(fresh_db):
    run_id = fresh_db.create_run("Duplicate execution test")
    controller = AgentController(fresh_db, llm=MockLLMAdapter())

    locked = fresh_db.try_lock_run(run_id)
    assert locked is True

    with pytest.raises(RuntimeError) as exc_info:
        controller.execute(run_id)
    assert str(exc_info.value) == "RUN_BUSY"

    fresh_db.update_run_status(run_id, "running")


# 9. Malformed diagnosis JSON (C6 -> fallback diagnosis with insufficient confidence without crashing)
def test_malformed_diagnosis_json(fresh_db):
    run_id = fresh_db.create_run("Malformed JSON test")
    failed_tool = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        error=ErrorInfo(type="CustomError", message="custom crash"),
    )
    fresh_db.record_event(failed_tool)

    mock_llm = MockLLMAdapter(text="Sorry, I cannot diagnose this failure properly ```xml <error>broken</error> ```")
    events = fresh_db.get_events(run_id)

    diag = diagnose(failed_tool, events, mock_llm)
    assert diag is not None
    assert diag.confidence == "insufficient"
    assert "Malformed diagnosis JSON" in diag.root_cause


# 10. Retry produces a DIFFERENT failure type than original -> triggers genuinely fresh diagnosis
def test_retry_different_failure_type_fresh_diagnosis(fresh_db):
    run_id = fresh_db.create_run("Different failure type retry test")
    
    failed_tool_1 = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        input={"code": "print(x)"},
        error=ErrorInfo(type="NameError", message="name 'x' is not defined"),
    )
    fresh_db.record_event(failed_tool_1)

    diag_1_event = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=failed_tool_1.event_id,
        event_type="diagnosis",
        component="diagnosis_engine",
        status="success",
    )
    fresh_db.record_event(diag_1_event)

    diag_1 = Diagnosis(
        failure_event_id=failed_tool_1.event_id,
        tier="deterministic",
        failure_type="NameError",
        root_cause="Undefined variable",
        cause_type="direct_observation",
        claims=[Claim(text="Undefined variable", evidence_event_ids=[failed_tool_1.event_id])],
        confidence="high",
        recovery_action_suggested="regenerate_code",
    )

    with patch("backend.recovery.engine.run_code") as mock_run_code:
        mock_run_code.return_value = {
            "success": False,
            "error_type": "TypeError",
            "error_message": "unsupported operand type(s) for +: 'int' and 'str'",
            "output": {},
            "latency_ms": 10,
        }
        retry_tool = attempt_recovery(run_id, failed_tool_1, diag_1, diag_1_event.event_id, fresh_db, 1)

    assert retry_tool is not None
    assert retry_tool.status == "failed"
    assert retry_tool.error.type == "TypeError"
    assert retry_tool.error.type != failed_tool_1.error.type

    mock_llm = MockLLMAdapter()
    fresh_diag_2 = diagnose(retry_tool, fresh_db.get_events(run_id), mock_llm)
    assert fresh_diag_2.failure_event_id == retry_tool.event_id
    assert fresh_diag_2.failure_event_id != diag_1.failure_event_id
    assert fresh_diag_2.failure_type == "TypeError"


# 11. Replay while provider and executor are unavailable
def test_replay_with_unavailable_provider_and_executor(fresh_db):
    run_id = fresh_db.create_run("Replay offline test")
    
    e1 = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        event_type="llm_call",
        component="gemini_adapter",
        status="success",
        output={"code": "print('hello')"},
    )
    fresh_db.record_event(e1)
    e2 = TraceEvent(
        run_id=run_id,
        step_id=fresh_db.next_step_id(run_id),
        turn_id=1,
        parent_event_id=e1.event_id,
        event_type="tool_call",
        component="constrained_executor",
        status="success",
        input={"code": "print('hello')"},
        output={"stdout": "hello\n"},
    )
    fresh_db.record_event(e2)

    with patch("backend.agent.llm_adapter.GeminiAdapter.call", side_effect=RuntimeError("Provider offline")), \
         patch("backend.executor.sandbox.run_code", side_effect=RuntimeError("Sandbox offline")):
        
        replayed_events = replay_run(run_id, fresh_db)

    assert replayed_events is not None
    assert len(replayed_events) == 2
    assert replayed_events[0]["event_id"] == e1.event_id
    assert replayed_events[1]["event_id"] == e2.event_id


# 12. Context pressure at exactly the budget boundary (tokens == CONTEXT_BUDGET)
def test_context_budget_boundary():
    content = "a" * (CONTEXT_BUDGET * 4)
    messages = [{"role": "user", "content": content}]

    res = manage_context(messages, goal="Boundary test")
    
    assert res["tokens"] == CONTEXT_BUDGET
    assert res["action"] == "blocked"
