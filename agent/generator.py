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
- Names shown in double quotes contain spaces or symbols: keep the quotes in your SQL.
- Never modify data: SELECT (or WITH ... SELECT) only.
- Reply with the SQL inside a ```sql code block and nothing else.

Schema:
{schema}"""

# Fix 2 (STRICT_RETRY): Phase 1 saw a retry "fix" 0 rows for 'Classic Rock' by quietly
# switching to 'Rock' - answering a different question. This forbids that.
STRICT_NOTE = ("Keep the user's exact question: do not change, loosen or drop any filter or value "
               "the user asked for. If the data really has no matching rows, return the same SQL again.")

TABLE_SELECT_PROMPT = """The database below is too large to show in full.
List the tables needed to answer the question, including tables used only for joining.
Reply with table names only, comma-separated, nothing else.

Tables:
{tables}

Question: {question}"""

# Matches ```sql ... ``` (or a bare ``` ... ```); non-greedy so we take the first block.
_CODE_BLOCK = re.compile(r"```(?:sql|sqlite)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def user_message(question: str, evidence: str | None = None) -> str:
    # BIRD's "evidence" is a human-written hint (e.g. "rate = count / total"). Only the benchmark uses it.
    return f"{question}\nHint: {evidence}" if evidence else question


def initial_messages(schema: str, question: str, evidence: str | None = None,
                     history: list[tuple[str, str]] = ()) -> list[dict]:
    """System prompt, then earlier (question, SQL) turns, then the new question.

    Follow-ups ("now only for 2017") work because the model sees the SQL it wrote last
    time as its own earlier reply. Result rows are left out to save context.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(schema=schema)}]
    for q, sql in history:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": f"```sql\n{sql}\n```"}]
    messages.append({"role": "user", "content": user_message(question, evidence)})
    return messages


def feedback_messages(llm_reply: str, problem: str, strict: bool = False) -> list[dict]:
    """Turns to append after a failed attempt: the model's own reply, then what went wrong."""
    note = f"\n{STRICT_NOTE}" if strict else ""
    return [
        {"role": "assistant", "content": llm_reply},
        {"role": "user", "content": f"{problem}{note}\nFix the query and reply with the corrected SQL only."},
    ]


def extract_sql(llm_reply: str) -> str:
    """Models usually wrap SQL in a ``` block, sometimes with prose around it. Take the code."""
    m = _CODE_BLOCK.search(llm_reply)
    return (m.group(1) if m else llm_reply).strip()


def estimate_tokens(messages: list[dict]) -> int:
    # shortcut: chars/2.4 measured on BIRD schemas with qwen2.5's tokenizer (2.4-2.8 chars/token);
    # deliberately pessimistic. Upgrade to a real tokenizer if estimates start to matter.
    return int(sum(len(m["content"]) for m in messages) / 2.4)


def table_select_messages(names_schema: str, question: str, evidence: str | None = None) -> list[dict]:
    return [{"role": "user", "content": TABLE_SELECT_PROMPT.format(
        tables=names_schema, question=user_message(question, evidence))}]


def parse_tables(llm_reply: str, valid: list[str]) -> list[str]:
    """Keep only real table names from the reply (case-insensitive), in schema order."""
    said = {w.strip().strip('`"[]').lower() for w in re.split(r"[,\n]", extract_sql(llm_reply))}
    return [t for t in valid if t.lower() in said]
