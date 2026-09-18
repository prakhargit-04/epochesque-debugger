from backend.config import INJECTION_TYPES


def inject_failure_at_boundary(injection_type: str, real_tool_input: dict) -> dict:
    if injection_type not in INJECTION_TYPES:
        raise ValueError(f"unknown injection_type: {injection_type}")
    if injection_type == "generated_code_failure":
        return {**real_tool_input, "code": "print(numbers)", "injection_type": injection_type}
    if injection_type == "tool_failure":
        return {**real_tool_input, "code": "raise ConnectionError('simulated transient failure')", "injection_type": injection_type}
    return {**real_tool_input, "code": "import json; json.loads('{not valid json')", "injection_type": injection_type}

