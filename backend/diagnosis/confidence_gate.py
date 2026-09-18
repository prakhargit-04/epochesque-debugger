from backend.config import ALLOWED_RECOVERY_ACTIONS, RECOVERABLE_TYPES


def compute_confidence(tier, citations_valid, has_citations, failure_type_matches_evidence):
    if tier == "deterministic":
        return "high"
    if not has_citations:
        return "insufficient"
    if not citations_valid:
        return "low"
    if not failure_type_matches_evidence:
        return "insufficient"
    return "medium"


def can_auto_recover(
    confidence,
    failure_type,
    action,
    recoverable_types=RECOVERABLE_TYPES,
    allowed_actions=ALLOWED_RECOVERY_ACTIONS,
) -> bool:
    if confidence not in ("high", "medium"):
        return False
    if failure_type not in recoverable_types:
        return False
    if action not in allowed_actions:
        return False
    return True
