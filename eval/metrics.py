"""Scoring: execution accuracy (EX) and failure reasons.

Two match rules:
  ex_match      - BIRD's official rule: same SET of row tuples (row order and duplicates
                  ignored, column order matters). Use this for numbers you compare to papers.
  lenient_match - also ignores column order and compares numbers to 2 decimals. The gap
                  between the two shows how many "failures" are only formatting.
"""
from agent.loop import AgentResult


def ex_match(pred_rows, gold_rows) -> bool:
    return set(map(tuple, pred_rows)) == set(map(tuple, gold_rows))


def _norm_row(row) -> tuple:
    def val(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f"{float(v):.2f}"
        return str(v)
    return tuple(sorted(val(v) for v in row))


def lenient_match(pred_rows, gold_rows) -> bool:
    return {_norm_row(r) for r in pred_rows} == {_norm_row(r) for r in gold_rows}


# (substring in the sqlite/guard error, reason label) - first match wins
_ERROR_REASONS = [
    ("no such column", "bad_column"),
    ("no such table", "bad_table"),
    ("no such function", "bad_function"),
    ("syntax error", "syntax"),
    ("incomplete input", "syntax"),
    ("ambiguous column", "ambiguous_column"),
    ("longer than", "timeout"),
    ("Only SELECT", "guard"), ("Forbidden keyword", "guard"), ("single statement", "guard"),
]


def failure_reason(r: AgentResult, ok: bool, ok_lenient: bool) -> str:
    """One label per question; '' when it passed."""
    if ok:
        return ""
    if r.result is None:  # no attempt ever executed: classify the last error
        err = r.attempts[-1].error or ""
        return next((label for s, label in _ERROR_REASONS if s in err), "sql_error")
    if ok_lenient:
        return "format_only"   # right values, different column order / rounding
    if not r.result.rows:
        return "empty_result"
    return "wrong_result"
