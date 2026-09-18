import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./epochesque.db")
MAX_EXECUTION_TIME = int(os.getenv("MAX_EXECUTION_TIME", "10"))
MAX_OUTPUT_SIZE = int(os.getenv("MAX_OUTPUT_SIZE", "51200"))
MAX_TRACE_PAYLOAD = int(os.getenv("MAX_TRACE_PAYLOAD", "102400"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
MAX_LLM_RETRIES = int(os.getenv("MAX_LLM_RETRIES", "1"))
MAX_EVENTS_PER_RUN = int(os.getenv("MAX_EVENTS_PER_RUN", "500"))
EVIDENCE_WINDOW_SIZE = int(os.getenv("EVIDENCE_WINDOW_SIZE", "6"))
EVIDENCE_PARENT_HOP_LIMIT = int(os.getenv("EVIDENCE_PARENT_HOP_LIMIT", "5"))
CONTEXT_BUDGET = int(os.getenv("CONTEXT_BUDGET", "8000"))
CONTEXT_TRIGGER_RATIO = float(os.getenv("CONTEXT_TRIGGER_RATIO", "0.8"))
LATEST_TURNS_PRESERVED = int(os.getenv("LATEST_TURNS_PRESERVED", "3"))
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
DEFAULT_MAX_TOKENS = int(os.getenv("DEFAULT_MAX_TOKENS", "500"))
DIAGNOSIS_MAX_TOKENS = int(os.getenv("DIAGNOSIS_MAX_TOKENS", "800"))

BLOCKED_NAMES = {"eval", "exec", "open"}
BLOCKED_IMPORTS = {"os", "subprocess", "socket", "shutil", "pathlib"}
RECOVERABLE_TYPES = {"NameError", "TypeError", "correctable_tool_input", "transient_tool_failure"}
ALLOWED_RECOVERY_ACTIONS = {"regenerate_code", "retry_tool", "correct_tool_input"}
INJECTION_TYPES = {"tool_failure", "malformed_tool_response", "generated_code_failure"}

# Demo free-tier accounting: retain exact token usage while pricing the demo at $0.
PRICING_TABLE = {
    "gemini-3.5-flash": {
        "input_per_1k": 0.0,
        "output_per_1k": 0.0,
    }
}
