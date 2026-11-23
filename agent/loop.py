"""The agent loop: generate SQL -> run in sandbox -> check result -> retry with feedback.

    attempt 1:  LLM writes SQL  -> sandbox runs it  -> OK? done.
                                    | error / suspicious result
    attempt 2:  LLM sees its SQL + the problem -> new SQL -> ...
    ... up to 1 + max_retries attempts.

"Suspicious" = ran fine but returned 0 rows or only NULLs (and, with zero_agg_suspicious,
a single row of 0/NULL like COUNT = 0). That is often a bug (misspelled WHERE value,
wrong join) but can be the true answer, so the feedback tells the model to return the
same SQL if it's sure - and if it does, we accept it instead of burning more retries.

Before the first attempt: if the full schema would overflow the model's context window,
an extra LLM call picks the relevant tables, and only those are shown (logged, never silent).
"""
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent import config, generator, llm
from agent.sandbox import GuardError, QueryResult, run_query
from agent.schema import _needs_quotes as needs_quotes
from agent.schema import bridge_tables, get_schema, list_tables
from agent.fanout import FANOUT_RULE, fanout_problem
from agent.sqlcheck import fix_keyword_aliases, shape_problem

log = logging.getLogger(__name__)

# Context kept free for the model's answer and up to 3 retry turns (SQL + error each).
RESERVED_TOKENS = 1024
# Earlier (question, SQL) turns sent with a follow-up question.
MAX_HISTORY_TURNS = 3


@dataclass
class Attempt:
    sql: str
    error: str | None           # None = this attempt was accepted
    latency_ms: float           # LLM call + query execution
    tokens_per_sec: float | None


@dataclass
class AgentResult:
    question: str
    result: QueryResult | None = None  # last result that executed without error (may be "suspicious")
    sql: str | None = None             # the SQL that produced `result`
    attempts: list[Attempt] = field(default_factory=list)
    prompt_tokens_est: int = 0
    selected_tables: list[str] | None = None  # set when table selection ran
    select_ms: float = 0                       # time spent on table selection
    ctx_overflow: bool = False                 # prompt still over budget after selection
    history_used: int = 0                      # earlier turns that fit in the prompt

    @property
    def ok(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].error is None


def _suspicious(r: QueryResult, zero_agg: bool = False) -> str | None:
    """Return a feedback message if the result looks wrong, else None."""
    if not r.rows:
        return ("The query ran but returned 0 rows. Check WHERE values against the sample rows "
                "(spelling, case, format) and the join conditions. "
                "If 0 rows is truly the correct answer, return the same query unchanged.")
    if all(v is None for row in r.rows for v in row):
        return ("The query ran but every value is NULL. Check the columns, joins and aggregates. "
                "If that is truly correct, return the same query unchanged.")
    # Fix 1 (SUSPECT_ZERO_AGG): COUNT/SUM over no matching rows gives one row of 0 - Phase 1's
    # "0 rows" check never saw it, so a misspelled filter silently answered "0".
    if zero_agg and len(r.rows) == 1 and all(v is None or v == 0 for v in r.rows[0]):
        return ("The query returned a single row of 0/NULL, which often means a filter matched "
                "nothing (e.g. a misspelled value or wrong join). Check WHERE values against the "
                "sample rows. If 0 is truly the correct answer, return the same query unchanged.")
    return None


def _keyword_hint(error: str) -> str:
    # Olist: the model aliased order_reviews as "or" and retried the same SQL 3 times,
    # because 'near "or": syntax error' doesn't say why. Name the cause when it's a keyword.
    m = re.search(r'near "(\w+)": syntax error', error)
    if m and needs_quotes(m.group(1)):
        return (f' "{m.group(1)}" is a reserved SQL keyword: if you used it as a table alias '
                f"or name, use a different alias (or double-quote the name).")
    return ""


def _fit_schema(db_path, question, evidence, detail, history, out: AgentResult,
                rules: tuple[str, ...] = ()) -> list[dict]:
    """Initial messages, shrunk to fit the context window in three steps (logged, never silent):
    1. drop older history turns, keeping the last one (a follow-up needs it most);
    2. show only the tables this question needs (one extra LLM call);
    3. drop the last history turn too.
    """
    budget = config.CONTEXT_LIMIT - RESERVED_TOKENS

    def build(schema, hist):
        m = generator.initial_messages(schema, question, evidence, hist, rules)
        return m, generator.estimate_tokens(m)

    schema = get_schema(db_path, detail)
    messages, est = build(schema, history)
    while est > budget and len(history) > 1:
        history = history[1:]
        log.info("prompt over budget: dropped oldest history turn (%d left)", len(history))
        messages, est = build(schema, history)

    if est > budget:
        log.warning("prompt ~%d tokens > budget %d: selecting tables first", est, budget)
        # A follow-up like "only for 2017" names no tables; the previous question does.
        ask = f"{history[-1][0]}\nFollow-up question: {question}" if history else question
        start = time.perf_counter()
        reply = llm.chat(generator.table_select_messages(get_schema(db_path, "names"), ask, evidence))
        out.select_ms = (time.perf_counter() - start) * 1000
        picked = generator.parse_tables(reply.text, list_tables(db_path))
        # Add bridge tables, so e.g. order_reviews + products also brings order_items.
        out.selected_tables = bridge_tables(db_path, picked) if picked else None
        log.info("selected tables: %s", out.selected_tables or "none recognised - keeping all")
        if out.selected_tables:
            schema = get_schema(db_path, detail, tuple(out.selected_tables))
            messages, est = build(schema, history)

    if est > budget and history:
        history = []
        log.info("prompt over budget: dropped the last history turn")
        messages, est = build(schema, history)

    out.prompt_tokens_est, out.history_used = est, len(history)
    if est > budget:
        out.ctx_overflow = True
        log.warning("prompt still ~%d tokens after selection; Ollama will truncate it", est)
    return messages


def run_agent(question: str, db_path: str | Path, max_retries: int = config.MAX_RETRIES, *,
              evidence: str | None = None, schema_detail: str = "full",
              zero_agg_suspicious: bool | None = None, strict_retry: bool | None = None,
              history: list[tuple[str, str]] | None = None,
              check_shape: bool | None = None, check_fanout: bool | None = None) -> AgentResult:
    """`history`: earlier (question, SQL) turns of this conversation, oldest first."""
    # None = use the .env switch; the benchmark passes explicit values.
    zero_agg = config.SUSPECT_ZERO_AGG if zero_agg_suspicious is None else zero_agg_suspicious
    strict = config.STRICT_RETRY if strict_retry is None else strict_retry
    shape = config.CHECK_SQL_SHAPE if check_shape is None else check_shape
    fanout = config.CHECK_FANOUT if check_fanout is None else check_fanout

    out = AgentResult(question)
    recent = list(history or [])[-MAX_HISTORY_TURNS:]
    messages = _fit_schema(db_path, question, evidence, schema_detail, recent, out,
                           (FANOUT_RULE,) if fanout else ())
    warned_sql = None  # SQL we flagged as suspicious last round
    failed = set()     # SQL that already errored: resending it is a wasted retry at temperature 0

    for n in range(1, max_retries + 2):  # first try + retries
        start = time.perf_counter()
        reply = llm.chat(messages)
        sql, renamed = fix_keyword_aliases(generator.extract_sql(reply.text))
        if renamed:
            log.info("renamed keyword table aliases: %s", ", ".join(renamed))
        try:
            result = run_query(db_path, sql)
            out.result, out.sql = result, sql
            # Same SQL resubmitted after a warning = model confirms the empty/zero answer.
            problem = None if sql == warned_sql else (
                _suspicious(result, zero_agg)
                or (shape_problem(sql, question) if shape else None)
                or (fanout_problem(sql, db_path) if fanout else None))
            warned_sql = sql if problem else None
        except (GuardError, TimeoutError, sqlite3.Error) as e:
            problem = f"The query failed with error: {e}" + _keyword_hint(str(e))
            if renamed:
                problem += (f" Note: your table alias {', '.join(renamed)} is an SQL keyword and was "
                            "renamed for this run; use short aliases that are not keywords (r, oi, p).")
            if sql in failed:
                problem += " You sent this exact query before and it failed the same way: rewrite it differently."
            failed.add(sql)
            warned_sql = None
        latency = (time.perf_counter() - start) * 1000

        out.attempts.append(Attempt(sql, problem, latency, reply.tokens_per_sec))
        log.info("attempt %d: %.0f ms | %s | %s", n, latency, problem or "OK", " ".join(sql.split()))
        if problem is None:
            break
        messages += generator.feedback_messages(reply.text, problem, strict)
    return out
