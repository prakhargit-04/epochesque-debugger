from backend.config import EVIDENCE_PARENT_HOP_LIMIT, EVIDENCE_WINDOW_SIZE


def build_evidence_window(events, failure_event):
    by_id = {e.event_id: e for e in events}
    allowed = {failure_event.event_id}

    current = failure_event
    for _ in range(EVIDENCE_PARENT_HOP_LIMIT):
        if not current.parent_event_id or current.parent_event_id not in by_id:
            break
        allowed.add(current.parent_event_id)
        current = by_id[current.parent_event_id]

    same_turn = [e for e in events if e.turn_id == failure_event.turn_id and e.step_id <= failure_event.step_id]
    for e in sorted(same_turn, key=lambda x: x.step_id)[-EVIDENCE_WINDOW_SIZE:]:
        allowed.add(e.event_id)

    originating = [
        e for e in events
        if e.turn_id == failure_event.turn_id and e.step_id <= failure_event.step_id
        and e.event_type == "agent_decision"
    ]
    if originating:
        allowed.add(max(originating, key=lambda x: x.step_id).event_id)

    return tuple(sorted(allowed))
