from backend.config import PRICING_TABLE


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = PRICING_TABLE.get(model, {"input_per_1k": 0.0, "output_per_1k": 0.0})
    return round((input_tokens / 1000.0) * pricing["input_per_1k"] + (output_tokens / 1000.0) * pricing["output_per_1k"], 8)
