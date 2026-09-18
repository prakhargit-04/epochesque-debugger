import json
from backend.diagnosis.diagnosis_engine import diagnose, extract_json_block
from backend.models.trace_event import ErrorInfo, TraceEvent


class MockLLMAdapter:
    def __init__(self, return_text):
        self.return_text = return_text

    def call(self, messages, system=None, max_tokens=800):
        return {"text": self.return_text, "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}}


def make_failed_event():
    return TraceEvent(
        run_id="run_1",
        step_id=1,
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        error=ErrorInfo(type="generated_code_failure", message="SyntaxError"),
    )


def test_extract_json_block():
    text1 = "Here is the diagnosis:\n```json\n{\"failure_type\": \"generated_code_failure\"}\n```"
    assert extract_json_block(text1) == '{"failure_type": "generated_code_failure"}'

    text2 = "```\n{\"failure_type\": \"test\"}\n```"
    assert extract_json_block(text2) == '{"failure_type": "test"}'

    text3 = '{"raw": "json"}'
    assert extract_json_block(text3) == '{"raw": "json"}'


def test_diagnose_malformed_json_handles_gracefully():
    failure_event = make_failed_event()
    events = [failure_event]
    mock_llm = MockLLMAdapter("This is totally not JSON at all!")

    diagnosis = diagnose(failure_event, events, mock_llm)

    assert diagnosis.confidence == "insufficient"
    assert diagnosis.claims == []
    assert diagnosis.recovery_action_suggested is None
    assert diagnosis.root_cause == "Malformed diagnosis JSON returned by model."


def test_diagnose_markdown_wrapped_json():
    failure_event = make_failed_event()
    events = [failure_event]
    json_payload = {
        "failure_type": "generated_code_failure",
        "root_cause": "Code contained invalid syntax.",
        "cause_type": "supported_inference",
        "claims": [{"text": "Code contained invalid syntax.", "evidence_event_ids": [failure_event.event_id]}],
        "recovery_action_suggested": "regenerate_code",
    }
    wrapped_text = f"Sure! Here is your output:\n```json\n{json.dumps(json_payload)}\n```"
    mock_llm = MockLLMAdapter(wrapped_text)

    diagnosis = diagnose(failure_event, events, mock_llm)

    assert diagnosis.tier == "llm"
    assert diagnosis.failure_type == "generated_code_failure"
    assert diagnosis.confidence in {"medium", "high"}
    assert diagnosis.recovery_action_suggested == "regenerate_code"


def test_c5_error_type_mismatch_results_in_insufficient_confidence():
    # C5: Valid citation, but cited event's error.type doesn't match claimed failure_type -> insufficient, not medium
    failure_event = TraceEvent(
        run_id="run_c5",
        step_id=1,
        turn_id=1,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        error=ErrorInfo(type="SyntaxError", message="invalid syntax"),
    )
    events = [failure_event]

    # Model claims failure_type is "ConnectionError", but cited event has error.type = "SyntaxError"
    json_payload = {
        "failure_type": "ConnectionError",
        "root_cause": "Network interface failed",
        "cause_type": "supported_inference",
        "claims": [{"text": "Network failed", "evidence_event_ids": [failure_event.event_id]}],
        "recovery_action_suggested": "retry_tool",
    }
    mock_llm = MockLLMAdapter(json.dumps(json_payload))

    diagnosis = diagnose(failure_event, events, mock_llm)

    assert diagnosis.tier == "llm"
    assert diagnosis.failure_type == "ConnectionError"
    assert diagnosis.confidence == "insufficient"

