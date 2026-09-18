def validate_citations(claims, allowed_event_ids) -> bool:
    allowed = set(allowed_event_ids)
    return all(eid in allowed for claim in claims for eid in claim.evidence_event_ids)


def has_citations(claims) -> bool:
    return any(bool(c.evidence_event_ids) for c in claims)
