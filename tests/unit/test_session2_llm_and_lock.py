import os
import pytest
from fastapi.testclient import TestClient

from backend.agent.controller import AgentController
from backend.agent.llm_adapter import MockLLMAdapter
from backend.api.main import app, db
from backend.db import DB
from backend.models.trace_event import TraceEvent


@pytest.fixture
def test_db():
    db_path = "test_session2.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    database = DB(db_path)
    database.init_schema()
    yield database
    if os.path.exists(db_path):
        os.remove(db_path)


# Test #13: LLM infra failure traced separately from diagnosis/recovery, retried at most once
def test_llm_infra_failure_traced_separately_and_retried_once(test_db):
    run_id = test_db.create_run("Infra failure goal")

    # 1. Permanent Infra Failure (all retries fail)
    failing_llm = MockLLMAdapter(raise_exception=TimeoutError("LLM API request timed out"))
    controller = AgentController(test_db, llm=failing_llm)

    events = controller.execute(run_id)

    # Asserts for Permanent Infra Failure:
    # LLM called exactly twice (1 initial + 1 retry via MAX_LLM_RETRIES=1)
    assert failing_llm.calls == 2

    # Exactly 1 failed llm_call event recorded in trace
    llm_events = [e for e in events if e.event_type == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0].status == "failed"
    assert llm_events[0].error is not None
    assert llm_events[0].error.type == "LLMProviderError"

    # Run status marked as failed
    run_state = test_db.get_run(run_id)
    assert run_state["status"] == "failed"

    # Infra failure MUST NEVER route to diagnosis or recovery
    diagnosis_events = [e for e in events if e.event_type == "diagnosis"]
    recovery_events = [e for e in events if e.event_type == "recovery"]
    assert len(diagnosis_events) == 0
    assert len(recovery_events) == 0


def test_llm_infra_transient_failure_recovers_on_retry(test_db):
    run_id = test_db.create_run("Transient infra failure goal")

    # First call fails with ConnectionError, second call succeeds with valid code
    transient_llm = MockLLMAdapter(
        responses=[
            ConnectionError("Transient connection reset"),
            "print('Hello world')",
        ]
    )
    controller = AgentController(test_db, llm=transient_llm)

    events = controller.execute(run_id)

    # 2 calls total made
    assert transient_llm.calls == 2

    # LLM call event succeeds
    llm_events = [e for e in events if e.event_type == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0].status == "success"
    assert llm_events[0].output["code"] == "print('Hello world')"


# Test #15: Concurrent /execute calls on the same run -> second returns 409 RUN_BUSY
def test_concurrent_execute_returns_409_run_busy(test_db):
    run_id = test_db.create_run("Locking test")

    # Lock the run by marking status = 'busy'
    test_db.update_run_status(run_id, "busy")

    mock_llm = MockLLMAdapter(text="print('Locked run')")
    controller = AgentController(test_db, llm=mock_llm)

    # Direct controller invocation must raise RuntimeError("RUN_BUSY")
    with pytest.raises(RuntimeError) as exc_info:
        controller.execute(run_id)
    assert str(exc_info.value) == "RUN_BUSY"

    # FastAPI endpoint test using TestClient
    client = TestClient(app)
    api_run_id = db.create_run("API lock test")
    db.update_run_status(api_run_id, "busy")

    response = client.post(f"/runs/{api_run_id}/execute")
    assert response.status_code == 409
    assert response.json()["detail"] == "RUN_BUSY"


def test_sequential_execute_calls_both_succeed(test_db):
    client = TestClient(app)
    create_res = client.post("/runs", json={"goal": "Sequential run test"})
    assert create_res.status_code == 200
    run_id = create_res.json()["run_id"]

    # First execution call: locks to busy during step, then releases lock upon completion
    res1 = client.post(f"/runs/{run_id}/execute")
    assert res1.status_code == 200

    # Verify run status is no longer busy after step 1 completes
    run_info = client.get(f"/runs/{run_id}").json()
    assert run_info["status"] != "busy"

    # Second execution call: should succeed cleanly without returning 409
    res2 = client.post(f"/runs/{run_id}/execute")
    assert res2.status_code == 200

