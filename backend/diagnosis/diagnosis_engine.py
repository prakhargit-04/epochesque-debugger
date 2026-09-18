import json
import re
import time

from backend.config import DIAGNOSIS_MAX_TOKENS, LLM_MODEL
from backend.diagnosis.citation_validator import has_citations, validate_citations
from backend.diagnosis.confidence_gate import compute_confidence
from backend.diagnosis.evidence_builder import build_evidence_window
from backend.models.diagnosis import Claim, Diagnosis

DETERMINISTIC_MAP = {
    "NameError": "Undefined variable referenced in generated code.",
    "TypeError": "Type mismatch in generated code operation.",
}

SYSTEM = """You are a diagnosis component. Return JSON only with:
{"failure_type": str, "root_cause": str, "cause_type": "direct_observation"|"supported_inference"|"unsupported_speculation", "claims": [{"text": str, "evidence_event_ids": [str]}], "recovery_action_suggested": str|null}
Only cite event IDs provided in the allowed evidence. Do not include confidence; the application computes it."""


def extract_json_block(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def diagnose(failure_event, events, llm_adapter):
    error_type = failure_event.error.type if failure_event.error else "Unknown"
    if error_type in DETERMINISTIC_MAP:
        root = DETERMINISTIC_MAP[error_type]
        return Diagnosis(
            failure_event_id=failure_event.event_id,
            tier="deterministic",
            failure_type=error_type,
            root_cause=root,
            cause_type="direct_observation",
            claims=[Claim(text=root, evidence_event_ids=[failure_event.event_id])],
            confidence="high",
            recovery_action_suggested="regenerate_code",
        )

    allowed_ids = build_evidence_window(events, failure_event)
    evidence = [e.model_dump(mode="json") for e in events if e.event_id in set(allowed_ids)]
    started = time.time()
    try:
        response = llm_adapter.call(
            [{"role": "user", "content": json.dumps({"failure_event": failure_event.model_dump(mode="json"), "allowed_event_ids": list(allowed_ids), "evidence": evidence})}],
            system=SYSTEM,
            max_tokens=DIAGNOSIS_MAX_TOKENS,
        )
    except Exception:
        # A diagnosis-provider outage must not crash or fabricate a recovery.
        response = {"text": ""}
    _ = time.time() - started
    raw_text = response.get("text", "").strip()

    parsed = {}
    claims = []
    try:
        cleaned = extract_json_block(raw_text)
        candidate = json.loads(cleaned)
        if isinstance(candidate, dict):
            parsed = candidate
            raw_claims = parsed.get("claims", [])
            if isinstance(raw_claims, list):
                for c in raw_claims:
                    if isinstance(c, dict):
                        claims.append(Claim(**c))
    except Exception:
        parsed = {}
        claims = []

    valid = validate_citations(claims, allowed_ids)
    present = has_citations(claims)
    failure_type = parsed.get("failure_type", error_type)
    cited_ids = {eid for claim in claims for eid in claim.evidence_event_ids}
    evidence_by_id = {event.event_id: event for event in events}
    matches = any(
        evidence_by_id.get(event_id)
        and evidence_by_id[event_id].error
        and evidence_by_id[event_id].error.type == failure_type
        for event_id in cited_ids
    )
    confidence = compute_confidence("llm", valid, present, matches)
    return Diagnosis(
        failure_event_id=failure_event.event_id,
        tier="llm",
        failure_type=failure_type,
        root_cause=parsed.get("root_cause", "Malformed diagnosis JSON returned by model."),
        cause_type=parsed.get("cause_type", "unsupported_speculation"),
        claims=claims,
        confidence=confidence,
        recovery_action_suggested=parsed.get("recovery_action_suggested"),
    )
