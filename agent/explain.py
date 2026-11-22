"""Turn the query result into a short plain-English answer (one extra LLM call)."""
from tabulate import tabulate

from agent import llm
from agent.sandbox import QueryResult

PREVIEW_ROWS = 10  # the model only needs a taste of the data, not all 1000 rows


def explain(question: str, sql: str, result: QueryResult) -> str:
    preview = tabulate(result.rows[:PREVIEW_ROWS], headers=result.columns)
    total = f"{len(result.rows)}{'+ (truncated)' if result.truncated else ''}"
    prompt = (
        f"Question: {question}\n\nSQL that was run:\n{sql}\n\n"
        f"Result ({total} rows, first {min(len(result.rows), PREVIEW_ROWS)} shown):\n{preview}\n\n"
        "In 1-2 sentences, answer the question from this result. "
        "Use only numbers that appear in the result. No SQL, no preamble."
    )
    return llm.chat([{"role": "user", "content": prompt}]).text.strip()
