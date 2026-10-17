"""Groq chat client that rotates API keys and falls back across models on rate limits.

Usage:
    from mnemo.llm import GroqPool
    llm = GroqPool.from_env()
    resp = llm.chat([{"role": "user", "content": "hi"}], model=llm.agent_model)
    print(resp.choices[0].message.content)
"""

import os
import threading
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError

DEFAULT_COOLDOWN_S = 30.0
MAX_WAIT_S = 120.0


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class GroqPool:
    """Round-robins requests across keys; on 429 it cools that (key, model) down and tries the next.

    Groq rate limits are per model, so a cooldown on one model does not block the others.
    When every key is cooling down for a model, the next model in `fallback_models` is tried.
    If everything is cooling down, it waits for the earliest cooldown to end (up to MAX_WAIT_S).
    """

    def __init__(self, keys: list[str], agent_model: str, extract_model: str, fallback_models: list[str]):
        if not keys:
            raise ValueError("No Groq API keys. Set GROQ_API_KEYS in .env")
        self.clients = [Groq(api_key=k, max_retries=0) for k in keys]
        self.agent_model = agent_model
        self.extract_model = extract_model
        self.fallback_models = fallback_models
        self._cooldown_until: dict[tuple[int, str], float] = {}
        self._next = 0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "GroqPool":
        load_dotenv()
        return cls(
            keys=_split(os.environ.get("GROQ_API_KEYS", "")),
            agent_model=os.environ.get("GROQ_AGENT_MODEL", "llama-3.3-70b-versatile"),
            extract_model=os.environ.get("GROQ_EXTRACT_MODEL", "openai/gpt-oss-120b"),
            fallback_models=_split(os.environ.get("GROQ_FALLBACK_MODELS", "")),
        )

    def _key_order(self) -> list[int]:
        with self._lock:
            start = self._next
            self._next = (self._next + 1) % len(self.clients)
        n = len(self.clients)
        return [(start + i) % n for i in range(n)]

    def chat(self, messages: list[dict], model: str | None = None, **kwargs):
        """Same arguments as groq `chat.completions.create`, minus `model` handling."""
        models = [model or self.agent_model] + [m for m in self.fallback_models if m != model]
        deadline = time.monotonic() + MAX_WAIT_S

        while True:
            for m in models:
                for i in self._key_order():
                    if self._cooldown_until.get((i, m), 0.0) > time.monotonic():
                        continue
                    try:
                        return self.clients[i].chat.completions.create(model=m, messages=messages, **kwargs)
                    except RateLimitError as e:
                        retry_after = e.response.headers.get("retry-after")
                        wait = float(retry_after) if retry_after else DEFAULT_COOLDOWN_S
                        self._cooldown_until[(i, m)] = time.monotonic() + wait

            soonest = min(self._cooldown_until.values())
            if soonest > deadline:
                raise RuntimeError("All Groq keys and models are rate-limited")
            time.sleep(max(0.0, soonest - time.monotonic()))
