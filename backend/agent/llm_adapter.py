from typing import Protocol

from backend.config import GOOGLE_API_KEY, LLM_MODEL


class LLMAdapter(Protocol):
    def call(self, messages: list, system: str = "", max_tokens: int = 1000) -> dict: ...


class GeminiAdapter:
    def __init__(self, api_key: str | None = None, model: str = LLM_MODEL):
        self.api_key = api_key or GOOGLE_API_KEY
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("GOOGLE_API_KEY is not configured")
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def call(self, messages: list, system: str = "", max_tokens: int = 1000) -> dict:
        client = self._get_client()
        prompt = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
        if system:
            prompt = f"SYSTEM: {system}\n\n{prompt}"
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"max_output_tokens": max_tokens},
        )
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        return {
            "text": getattr(response, "text", "") or "",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }


class MockLLMAdapter:
    def __init__(self, text: str = "print(numbers)", responses: list | None = None, raise_exception: Exception | None = None):
        self.text = text
        self.responses = responses
        self.raise_exception = raise_exception
        self.calls = 0

    def call(self, messages: list, system: str = "", max_tokens: int = 1000) -> dict:
        self.calls += 1
        if self.raise_exception:
            raise self.raise_exception
        if self.responses and len(self.responses) > 0:
            res = self.responses[min(self.calls - 1, len(self.responses) - 1)]
            if isinstance(res, Exception):
                raise res
            if isinstance(res, str):
                return {"text": res, "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
            return res
        return {"text": self.text, "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
