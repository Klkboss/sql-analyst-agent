"""Turn the query result into a short plain-English answer (one extra LLM call)."""
from tabulate import tabulate

from agent import llm
from agent.sandbox import QueryResult

PREVIEW_ROWS = 20  # enough for a year of months or a top-N; the full result is shown as a table anyway


def explain(question: str, sql: str, result: QueryResult) -> str:
    # floatfmt: tabulate's default prints 1441250.5 as 1.44125e+06, which the model then repeats.
    preview = tabulate(result.rows[:PREVIEW_ROWS], headers=result.columns, floatfmt=".2f")
    total = f"{len(result.rows)}{'+ (truncated)' if result.truncated else ''}"
    prompt = (
        f"Question: {question}\n\nSQL that was run:\n{sql}\n\n"
        f"Result ({total} rows, first {min(len(result.rows), PREVIEW_ROWS)} shown):\n{preview}\n\n"
        "In 1-2 sentences, answer the question from this result. The full table is shown to the "
        "user, so give the main finding (top items, totals, trend) instead of listing every row. "
        "Use only numbers that appear in the result. No SQL, no preamble."
    )
    return llm.chat([{"role": "user", "content": prompt}]).text.strip()
