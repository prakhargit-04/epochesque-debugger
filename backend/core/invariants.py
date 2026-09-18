from backend.diagnosis.citation_validator import validate_citations
from backend.diagnosis.evidence_builder import build_evidence_window
from backend.models.diagnosis import Claim


class InvariantViolation(RuntimeError):
    pass


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in {"timestamp", "attempt_id"}}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def assert_invariants(run_id, db):
    events = db.get_events(run_id)
    steps = [e.step_id for e in events]
    if steps != sorted(steps) or len(steps) != len(set(steps)):
        raise InvariantViolation("I1: step_id is not strictly increasing/unique")

    diagnoses = [e for e in events if e.event_type == "diagnosis"]
    for diagnosis in diagnoses:
        confidence = diagnosis.output.get("confidence")
        raw_claims = diagnosis.output.get("claims", [])
        claims = [Claim(**c) if isinstance(c, dict) else c for c in raw_claims]
        failure_event_id = diagnosis.input.get("failure_event_id")
        failure_event = db.get_event(failure_event_id) if failure_event_id else None
        if failure_event:
            allowed_ids = build_evidence_window(events, failure_event)
            if not validate_citations(claims, allowed_ids) and confidence in {"high", "medium"}:
                raise InvariantViolation("I3: invalid citations resulted in high/medium confidence")

        if confidence in {"low", "insufficient"}:
            later = [e for e in events if e.step_id > diagnosis.step_id and e.event_type == "recovery"]
            if any(e.input.get("diagnosis_event_id") == diagnosis.event_id for e in later):
                raise InvariantViolation("I4: low/insufficient confidence triggered recovery")

    recovery_attempts = [e.output.get("attempt", 0) for e in events if e.event_type == "recovery"]
    if any(a > 2 for a in recovery_attempts):
        raise InvariantViolation("I5: recovery attempts exceed 2")

    for recovery in [e for e in events if e.event_type == "recovery"]:
        failed_input = _normalize(recovery.input.get("failed_input", {}))
        modified_input = _normalize(recovery.output.get("modified_input", {}))
        if failed_input == modified_input:
            raise InvariantViolation("I6: retry input did not materially differ")
