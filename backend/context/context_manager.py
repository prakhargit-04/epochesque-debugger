from backend.config import CONTEXT_BUDGET, CONTEXT_TRIGGER_RATIO, LATEST_TURNS_PRESERVED


def estimate_tokens(messages) -> int:
    if not messages:
        return 0
    total = 0
    for m in messages:
        if isinstance(m, dict):
            content = str(m.get("content", ""))
        else:
            content = str(m)
        total += max(1, len(content) // 4)
    return max(1, total)


def manage_context(messages: list, goal: str, naive_mode: bool = False) -> dict:
    initial_tokens = estimate_tokens(messages)

    if naive_mode:
        # Naive oldest-truncation mode (drops oldest turns sequentially, ignoring requirement_capture)
        if initial_tokens < CONTEXT_BUDGET:
            return {"messages": messages, "tokens": initial_tokens, "action": "none"}
        trimmed = list(messages)
        open_turn = trimmed[-1]
        history = trimmed[:-1]
        while history and estimate_tokens([open_turn] + history) >= CONTEXT_BUDGET:
            history.pop(0)  # naive oldest drop
        res_messages = history + [open_turn]
        res_tokens = estimate_tokens(res_messages)
        return {"messages": res_messages, "tokens": res_tokens, "action": "naive_truncated"}

    if initial_tokens < CONTEXT_BUDGET * CONTEXT_TRIGGER_RATIO:
        return {"messages": messages, "tokens": initial_tokens, "action": "none"}

    if not messages:
        return {"messages": messages, "tokens": 0, "action": "none"}

    open_turn = messages[-1]
    completed_turns = messages[:-1]

    if not completed_turns:
        if initial_tokens >= CONTEXT_BUDGET:
            return {"messages": messages, "tokens": initial_tokens, "action": "blocked"}
        return {"messages": messages, "tokens": initial_tokens, "action": "none"}

    # 1. Identify Pinned Turns (Turn 1 / Goal turn + requirement_capture=True turns)
    pinned = []
    turn_1 = completed_turns[0]
    pinned.append(turn_1)

    remaining_completed = completed_turns[1:]

    # 2. Preserve Latest Completed Turns (LATEST_TURNS_PRESERVED, excluding open_turn)
    num_latest = min(LATEST_TURNS_PRESERVED, len(remaining_completed))
    if num_latest > 0:
        latest_N = remaining_completed[-num_latest:]
        middle_turns = remaining_completed[:-num_latest]
    else:
        latest_N = []
        middle_turns = remaining_completed

    eligible = []
    for t in middle_turns:
        is_req = (
            isinstance(t, dict) and (
                t.get("requirement_capture") is True or
                t.get("metadata", {}).get("requirement_capture") is True or
                "IMPORTANT REQUIREMENT" in str(t.get("content", ""))
            )
        )
        if is_req:
            pinned.append(t)
        else:
            eligible.append(t)

    # 3. Trim lowest-information eligible turns first
    current_eligible = list(eligible)

    def assemble(elig):
        kept_ids = set(id(m) for m in (pinned + elig + latest_N + [open_turn]))
        return [m for m in messages if id(m) in kept_ids]

    cand_messages = assemble(current_eligible)
    cand_tokens = estimate_tokens(cand_messages)
    action_taken = "trimmed"

    while cand_tokens >= CONTEXT_BUDGET * CONTEXT_TRIGGER_RATIO and current_eligible:
        current_eligible.pop(0)
        cand_messages = assemble(current_eligible)
        cand_tokens = estimate_tokens(cand_messages)

    # 4. Summarize remaining eligible turns if still over budget
    if cand_tokens >= CONTEXT_BUDGET and current_eligible:
        action_taken = "summarized"
        requirements_found = [
            str(t.get("content", "")) for t in pinned if "REQUIREMENT" in str(t.get("content", ""))
        ]
        summary_payload = {
            "summary": "Completed intermediate data processing turns",
            "facts": {},
            "decisions": ["execute_generated_code"],
            "requirements": requirements_found,
            "unresolved": [],
        }
        summary_turn = {
            "role": "system",
            "content": f"Structured Context Summary: {summary_payload}",
        }
        current_eligible = [summary_turn]
        cand_messages = assemble(current_eligible)
        cand_tokens = estimate_tokens(cand_messages)

    # 5. Hard Postcondition Enforcement
    if cand_tokens >= CONTEXT_BUDGET:
        return {"messages": cand_messages, "tokens": cand_tokens, "action": "blocked"}

    return {"messages": cand_messages, "tokens": cand_tokens, "action": action_taken}
