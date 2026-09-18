from backend.config import ALLOWED_RECOVERY_ACTIONS, INJECTION_TYPES, PRICING_TABLE


def test_config_is_loaded():
    assert "regenerate_code" in ALLOWED_RECOVERY_ACTIONS
    assert "generated_code_failure" in INJECTION_TYPES
    assert "gemini-3.5-flash" in PRICING_TABLE
