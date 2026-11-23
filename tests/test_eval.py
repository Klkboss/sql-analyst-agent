from agent.loop import AgentResult, Attempt
from agent.sandbox import QueryResult
from eval.bird import FIELDS, done_ids
from eval.metrics import ex_match, failure_reason, lenient_match


def test_ex_ignores_row_order_and_duplicates_but_not_column_order():
    gold = [(1, "a"), (2, "b")]
    assert ex_match([(2, "b"), (1, "a"), (1, "a")], gold)
    assert not ex_match([("a", 1), ("b", 2)], gold)
    assert not ex_match([(1, "a")], gold)


def test_lenient_ignores_column_order_and_rounding():
    assert lenient_match([("a", 1.0000001)], [(1, "a")])
    assert not lenient_match([("a", 1.1)], [(1, "a")])


def _result(rows=None, error=None):
    r = AgentResult("q", result=QueryResult(["x"], rows, False) if rows is not None else None)
    r.attempts.append(Attempt("SELECT x", error, 1.0, None))
    return r


def test_failure_reasons():
    assert failure_reason(_result([(1,)]), True, True) == ""
    assert failure_reason(_result(error="The query failed with error: no such column: x"), False, False) == "bad_column"
    assert failure_reason(_result(error="weird"), False, False) == "sql_error"
    assert failure_reason(_result([(1,)]), False, True) == "format_only"
    assert failure_reason(_result([]), False, False) == "empty_result"
    assert failure_reason(_result([(2,)]), False, False) == "wrong_result"


def test_resume_skips_done_but_retries_agent_errors(tmp_path):
    import csv
    path = tmp_path / "run.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerow({"question_id": 1, "reason": ""})
        w.writerow({"question_id": 2, "reason": "wrong_result"})
        w.writerow({"question_id": 3, "reason": "agent_error"})
    assert done_ids(path) == {1, 2}
    assert done_ids(tmp_path / "missing.csv") == set()
