"""One interface for every LLM backend: chat(messages) -> LLMResponse.

Design decision: plain HTTP with `requests` instead of the ollama/groq SDKs.
Both APIs take the same {"role", "content"} message list, so one function per
provider is ~15 lines, and the rest of the agent never knows which is in use.
Adding a provider later = one more _call_x function + one line in chat().
"""
import logging
import time
from dataclasses import dataclass

import requests

from agent import config

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    latency_ms: float
    tokens_per_sec: float | None  # generation speed; None if the provider doesn't report it


def chat(messages: list[dict]) -> LLMResponse:
    """Send a chat to the configured provider and return the reply text plus timing."""
    start = time.perf_counter()
    if config.LLM_PROVIDER == "ollama":
        text, tps = _call_ollama(messages)
    elif config.LLM_PROVIDER == "groq":
        text, tps = _call_groq(messages)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {config.LLM_PROVIDER!r} (use ollama or groq)")
    latency_ms = (time.perf_counter() - start) * 1000
    log.info("LLM %s: %.0f ms, %s tok/s", config.LLM_PROVIDER, latency_ms,
             f"{tps:.1f}" if tps else "n/a")
    return LLMResponse(text, latency_ms, tps)


def _call_ollama(messages: list[dict]) -> tuple[str, float | None]:
    options = {"temperature": 0, "num_ctx": config.OLLAMA_NUM_CTX}  # temp 0 = repeatable SQL
    if config.OLLAMA_NUM_GPU is not None:  # leave unset so Ollama picks GPU/CPU split itself
        options["num_gpu"] = config.OLLAMA_NUM_GPU
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json={
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": options,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,  # top-level field, not an option
        },
        timeout=300,  # first call may include loading the model from disk
    )
    resp.raise_for_status()
    data = resp.json()
    # eval_count = tokens generated, eval_duration = nanoseconds spent generating them
    tps = data["eval_count"] / data["eval_duration"] * 1e9 if data.get("eval_duration") else None
    return data["message"]["content"], tps


def _call_groq(messages: list[dict]) -> tuple[str, float | None]:
    if not config.GROQ_API_KEY:
        raise RuntimeError("LLM_PROVIDER=groq but GROQ_API_KEY is empty in .env")
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",  # OpenAI-compatible endpoint
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json={"model": config.GROQ_MODEL, "messages": messages, "temperature": 0},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage", {})
    # Groq adds completion_time (seconds) to the standard OpenAI usage block
    tps = usage["completion_tokens"] / usage["completion_time"] if usage.get("completion_time") else None
    return data["choices"][0]["message"]["content"], tps
