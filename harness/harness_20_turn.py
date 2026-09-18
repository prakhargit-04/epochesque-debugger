import json
from backend.agent.controller import AgentController
from backend.agent.llm_adapter import GeminiAdapter, MockLLMAdapter
from backend.context.context_manager import manage_context
from backend.db import DB
from backend.models.trace_event import ErrorInfo, TraceEvent


class SmartMockHarnessLLM:
    """Mock LLM adapter for harness testing that parses the prompt context to answer."""

    def __init__(self):
        self.calls = 0

    def call(self, messages, system="", max_tokens=1000):
        self.calls += 1
        prompt_text = json.dumps(messages)
        if "sorted descending" in prompt_text.lower() or "descending" in prompt_text.lower():
            return {
                "text": "The sort order you requested in turn 3 was sorted descending.",
                "usage": {"input_tokens": 120, "output_tokens": 15, "total_tokens": 135},
            }
        else:
            return {
                "text": "I do not have access to any requirement specifying a sort order in the context.",
                "usage": {"input_tokens": 120, "output_tokens": 15, "total_tokens": 135},
            }


def build_20_turn_trace(db: DB, run_id: str) -> list:
    """Constructs Turns 1-19 as real trace events, with real context pressure exceeding CONTEXT_BUDGET."""
    events = []

    # Turn 1: Goal setting
    t1 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=1,
        event_type="agent_decision",
        component="user_interface",
        status="success",
        input={"goal": "process this list of numbers and save results as CSV"},
        output={"decision": "initialize_task"},
    )
    db.record_event(t1)
    events.append(t1)

    # Turn 2: Noise
    t2 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=2,
        event_type="llm_call",
        component="llm_adapter",
        status="success",
        input={"turn": 2},
        output={"code": "# Noise turn 2 filler code\n" + "x = 1\n" * 50},
    )
    db.record_event(t2)
    events.append(t2)

    # Turn 3: IMPORTANT REQUIREMENT: output must be sorted descending (requirement_capture=True)
    t3 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=3,
        event_type="agent_decision",
        component="user_interface",
        status="success",
        input={"requirement": "IMPORTANT REQUIREMENT: output must be sorted descending"},
        output={"requirement_capture": True},
        metadata={"requirement_capture": True},
    )
    db.record_event(t3)
    events.append(t3)

    # Turn 4: Noise
    t4 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=4,
        event_type="llm_call",
        component="llm_adapter",
        status="success",
        input={"turn": 4},
        output={"code": "# Noise turn 4 filler code\n" + "y = 2\n" * 50},
    )
    db.record_event(t4)
    events.append(t4)

    # Turn 5: Tool call success
    t5 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=5,
        event_type="tool_call",
        component="constrained_executor",
        status="success",
        input={"code": "data = [1, 2, 3]"},
        output={"stdout": "data loaded\n"},
    )
    db.record_event(t5)
    events.append(t5)

    # Turn 6: Noise
    t6 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=6,
        event_type="llm_call",
        component="llm_adapter",
        status="success",
        input={"turn": 6},
        output={"code": "# Noise turn 6 filler code\n" + "z = 3\n" * 50},
    )
    db.record_event(t6)
    events.append(t6)

    # Turn 7: Injected failure (NameError)
    t7 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=7,
        event_type="tool_call",
        component="constrained_executor",
        status="failed",
        input={"code": "print(undefined_variable_numbers)"},
        output={"stderr": "NameError: name 'undefined_variable_numbers' is not defined"},
        error=ErrorInfo(type="NameError", message="name 'undefined_variable_numbers' is not defined"),
    )
    db.record_event(t7)
    events.append(t7)

    # Turn 8: Recovery success
    t8 = TraceEvent(
        run_id=run_id,
        step_id=db.next_step_id(run_id),
        turn_id=8,
        event_type="recovery",
        component="recovery_engine",
        status="success",
        input={"failed_step": t7.step_id},
        output={"attempt": 1, "action": "regenerate_code", "modified_input": {"code": "numbers = [5, 2, 9, 1]\nprint(numbers)"}},
    )
    db.record_event(t8)
    events.append(t8)

    # Turns 9-19: Long filler turns generating REAL context pressure (exceeding CONTEXT_BUDGET=8000)
    # Each filler turn contains ~3,000 characters (~750 tokens), generating ~9,000 tokens total across filler turns!
    large_filler = "filler_data = [" + ", ".join(f"'record_{i}_value_{i*10}'" for i in range(150)) + "]\n"
    for turn in range(9, 20):
        t_filler = TraceEvent(
            run_id=run_id,
            step_id=db.next_step_id(run_id),
            turn_id=turn,
            event_type="llm_call",
            component="llm_adapter",
            status="success",
            input={"turn": turn, "filler": f"Turn {turn} processing payload"},
            output={"code": f"# Turn {turn} heavy filler content\n" + large_filler},
        )
        db.record_event(t_filler)
        events.append(t_filler)

    return events


def run_20_turn_harness(db: DB, llm=None, context_enabled: bool = True) -> dict:
    """
    Executes the 20-turn cost-bounded harness (only ONE real LLM call at turn 20).
    Returns dict containing:
      - response_text: The actual string response from turn 20
      - requirement_preserved: True if response correctly references 'sorted descending'
      - tokens_before: Estimated context tokens before management
      - tokens_after: Estimated context tokens after management
    """
    run_id = db.create_run(f"20-turn harness (context_enabled={context_enabled})")
    llm_adapter = llm or SmartMockHarnessLLM()

    # Build Turns 1-19 trace events
    events = build_20_turn_trace(db, run_id)

    # Construct messages sequence representing Turns 1-19
    messages = []
    for e in events:
        role = "user" if e.event_type in ("agent_decision", "context_update") else "assistant"
        content_str = str(e.input.get("goal") or e.input.get("requirement") or e.output.get("code") or e.output.get("stdout") or e.input)
        item = {
            "role": role,
            "content": content_str,
            "turn_id": e.turn_id,
        }
        if e.output.get("requirement_capture") or e.metadata.get("requirement_capture"):
            item["requirement_capture"] = True
        messages.append(item)

    # Turn 20 probe prompt (the one real LLM call)
    turn_20_prompt = {"role": "user", "content": "what was the sort order I asked for?", "turn_id": 20}
    messages.append(turn_20_prompt)

    goal = "process this list of numbers and save results as CSV"

    if context_enabled:
        context_res = manage_context(messages, goal, naive_mode=False)
    else:
        context_res = manage_context(messages, goal, naive_mode=True)

    managed_messages = context_res["messages"]
    tokens_after = context_res["tokens"]

    # Execute the ONE genuine LLM call for Turn 20
    llm_response = llm_adapter.call(managed_messages, system="You are an agent memory probe.", max_tokens=200)
    response_text = llm_response.get("text", "")

    # Inspect real response text content (never a tautology)
    requirement_found = "sorted descending" in response_text.lower() or "descending" in response_text.lower()

    return {
        "run_id": run_id,
        "context_enabled": context_enabled,
        "response_text": response_text,
        "requirement_preserved": requirement_found,
        "tokens_after": tokens_after,
        "managed_action": context_res["action"],
    }
