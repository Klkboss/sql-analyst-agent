"""Prompt building for SQL generation, and pulling the SQL back out of the LLM reply.

Design decision: retries are a growing chat, not a fresh prompt each time.
The model sees its own previous SQL (as an assistant turn) followed by the error
(as a user turn) - the same way a person would say "that failed, here's why".
"""
import re

SYSTEM_PROMPT = """You are an expert SQLite analyst.
Write ONE SQLite SELECT query that answers the user's question using the schema below.
Rules:
- Use only tables and columns that exist in the schema.
- Join tables using the REFERENCES (foreign key) columns shown.
- Match text values exactly as they appear in the sample rows.
- Never modify data: SELECT (or WITH ... SELECT) only.
- Reply with the SQL inside a ```sql code block and nothing else.

Schema:
{schema}"""

# Matches ```sql ... ``` (or a bare ``` ... ```); non-greedy so we take the first block.
_CODE_BLOCK = re.compile(r"```(?:sql|sqlite)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def initial_messages(schema: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(schema=schema)},
        {"role": "user", "content": question},
    ]


def feedback_messages(llm_reply: str, problem: str) -> list[dict]:
    """Turns to append after a failed attempt: the model's own reply, then what went wrong."""
    return [
        {"role": "assistant", "content": llm_reply},
        {"role": "user", "content": f"{problem}\nFix the query and reply with the corrected SQL only."},
    ]


def extract_sql(llm_reply: str) -> str:
    """Models usually wrap SQL in a ``` block, sometimes with prose around it. Take the code."""
    m = _CODE_BLOCK.search(llm_reply)
    return (m.group(1) if m else llm_reply).strip()
