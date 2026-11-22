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
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_NUM_CTX = int(_env("OLLAMA_NUM_CTX", "4096"))
OLLAMA_NUM_GPU = int(_env("OLLAMA_NUM_GPU")) if _env("OLLAMA_NUM_GPU") else None  # None = auto
OLLAMA_KEEP_ALIVE = _env("OLLAMA_KEEP_ALIVE", "10m")

GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL", "llama-3.3-70b-versatile")

# Sandbox and agent-loop limits
QUERY_TIMEOUT_S = 10
MAX_ROWS = 1000
MAX_RETRIES = 3
