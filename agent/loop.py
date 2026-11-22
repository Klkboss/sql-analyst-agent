"""The agent loop: generate SQL -> run in sandbox -> check result -> retry with feedback.

    attempt 1:  LLM writes SQL  -> sandbox runs it  -> OK? done.
                                    | error / suspicious result
    attempt 2:  LLM sees its SQL + the problem -> new SQL -> ...
    ... up to 1 + MAX_RETRIES attempts.

"Suspicious" = ran fine but returned 0 rows or only NULLs. That is often a bug
(wrong spelling in a WHERE, wrong join) but can be the true answer, so the
feedback tells the model to return the same SQL if it's sure - and if it does,
we accept it instead of burning more retries.
"""
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent import config, generator, llm
from agent.sandbox import GuardError, QueryResult, run_query
from agent.schema import get_schema

log = logging.getLogger(__name__)


@dataclass
class Attempt:
    sql: str
    error: str | None           # None = this attempt was accepted
    latency_ms: float           # LLM call + query execution
    tokens_per_sec: float | None


@dataclass
class AgentResult:
    question: str
    result: QueryResult | None  # last result that executed without error (may be "suspicious")
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def sql(self) -> str | None:
        return self.attempts[-1].sql if self.attempts else None

    @property
    def ok(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].error is None


def _suspicious(r: QueryResult) -> str | None:
    """Return a feedback message if the result looks wrong, else None."""
    if not r.rows:
        return ("The query ran but returned 0 rows. Check WHERE values against the sample rows "
                "(spelling, case, format) and the join conditions. "
                "If 0 rows is truly the correct answer, return the same query unchanged.")
    if all(v is None for row in r.rows for v in row):
        return ("The query ran but every value is NULL. Check the columns, joins and aggregates. "
                "If that is truly correct, return the same query unchanged.")
    return None


def run_agent(question: str, db_path: str | Path, max_retries: int = config.MAX_RETRIES) -> AgentResult:
    messages = generator.initial_messages(get_schema(db_path), question)
    out = AgentResult(question, result=None)
    warned_sql = None  # SQL we flagged as suspicious last round

    for n in range(1, max_retries + 2):  # first try + retries
        start = time.perf_counter()
        reply = llm.chat(messages)
        sql = generator.extract_sql(reply.text)
        try:
            result = run_query(db_path, sql)
            out.result = result
            # Same SQL resubmitted after a warning = model confirms the empty/NULL answer.
            problem = None if sql == warned_sql else _suspicious(result)
            warned_sql = sql if problem else None
        except (GuardError, TimeoutError, sqlite3.Error) as e:
            problem = f"The query failed with error: {e}"
            warned_sql = None
        latency = (time.perf_counter() - start) * 1000

        out.attempts.append(Attempt(sql, problem, latency, reply.tokens_per_sec))
        log.info("attempt %d: %.0f ms | %s | %s", n, latency, problem or "OK", " ".join(sql.split()))
        if problem is None:
            break
        messages += generator.feedback_messages(reply.text, problem)
    return out
