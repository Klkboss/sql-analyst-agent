"""All settings in one place, read once from environment / .env.

Keeping config in a plain module (not a class) means any file can do
`from agent import config` and read `config.OLLAMA_MODEL` - nothing to pass around.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env from the working directory if present; real env vars win


def _env(name: str, default: str = "") -> str:
    # Treat "set but empty" the same as "not set", so `OLLAMA_NUM_GPU=` means auto.
    return os.getenv(name) or default


LLM_PROVIDER = _env("LLM_PROVIDER", "ollama").lower()

OLLAMA_HOST = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
# Default chosen by the BIRD benchmark: beat the 7B coder and fits fully in 6 GB VRAM.
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
OLLAMA_NUM_CTX = int(_env("OLLAMA_NUM_CTX", "4096"))
OLLAMA_NUM_GPU = int(_env("OLLAMA_NUM_GPU")) if _env("OLLAMA_NUM_GPU") else None  # None = auto
OLLAMA_KEEP_ALIVE = _env("OLLAMA_KEEP_ALIVE", "10m")

GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL", "llama-3.3-70b-versatile")

# Sandbox and agent-loop limits (read at call time, so the benchmark can raise them)
QUERY_TIMEOUT_S = 10
MAX_ROWS = 1000
MAX_RETRIES = 3

# Prompt budget in tokens. Over this, the agent first picks relevant tables.
# Groq models have far bigger windows, so the limit only really bites on Ollama.
CONTEXT_LIMIT = OLLAMA_NUM_CTX if LLM_PROVIDER == "ollama" else 32_000

# Phase 2 switches, measured on BIRD (eval/results/summary.md).
# Zero-aggregate check: on by default (fixed 2, broke 0 on the 4B).
SUSPECT_ZERO_AGG = _env("SUSPECT_ZERO_AGG", "1") == "1"  # a single 0/NULL row counts as suspicious
# Strict retry prompt: off by default - tested, no gain (fixed 0, broke 1).
STRICT_RETRY = _env("STRICT_RETRY", "0") == "1"
