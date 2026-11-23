"""Run LLM-written SQL safely: a fast SQL guard, then a locked-down SQLite connection.

Defense in depth - the LLM's output is untrusted input, so no single check is trusted alone:
  1. check_sql()   - text guard: one SELECT / WITH statement only. Cheap, and gives the
                     LLM a clear error message to fix. But text checks can be fooled.
  2. mode=ro URI   - SQLite opens the file read-only at the OS level (and won't create
                     a new empty DB if the path is wrong).
  3. query_only    - PRAGMA that makes SQLite refuse any write on this connection.
  4. authorizer    - SQLite asks our callback before every operation while compiling
                     the statement; we allow only reads. This is the real guarantee.
Plus resource limits: a 10s wall-clock timeout and a 1000-row cap.
"""
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agent import config


class GuardError(ValueError):
    """The SQL was rejected before it reached the database."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool  # True if the query returned more than max_rows rows


# --- 1. SQL guard ---------------------------------------------------------------

# One regex that matches, in order: '...' strings, "..." identifiers, -- comments, /* */ comments.
_TOKENS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?(?:\*/|$)", re.DOTALL)
# Write/admin keywords. REPLACE is left out because REPLACE() is also a normal string function;
# `REPLACE INTO` is still caught by the first-keyword check and by the authorizer.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b", re.I
)


def strip_literals(sql: str) -> str:
    """SQL with comments removed and string literals emptied, for keyword/shape checks."""
    return _TOKENS.sub(lambda m: " " if m.group().startswith(("--", "/*")) else "''", sql)


def check_sql(sql: str) -> None:
    """Raise GuardError unless `sql` is a single SELECT (or WITH ... SELECT) statement."""
    # Blank out strings and comments first, so "SELECT 'DROP'" passes and
    # "/* hi */ DELETE ..." doesn't hide its first keyword.
    code = strip_literals(sql).strip().rstrip(";").strip()
    if not code:
        raise GuardError("Empty query.")
    if ";" in code:
        raise GuardError("Only a single statement is allowed (found ';' between statements).")
    first = code.split(None, 1)[0].upper()
    if first not in ("SELECT", "WITH"):
        raise GuardError(f"Only SELECT queries are allowed, got {first}.")
    if m := _FORBIDDEN.search(code):
        raise GuardError(f"Forbidden keyword {m.group().upper()} - only read-only SELECT is allowed.")


# --- 2-4. Locked-down connection ------------------------------------------------

# SQLite action codes our authorizer lets through: reading tables/columns, running
# SELECT, calling functions (COUNT, ROUND...) and recursive CTEs. Everything else is denied.
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    sqlite3.SQLITE_RECURSIVE}


def _authorizer(action, *_args):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite file read-only. Also used by schema.py for trusted metadata queries."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def run_query(db_path: str | Path, sql: str, timeout_s: float | None = None,
              max_rows: int | None = None) -> QueryResult:
    """Guard, then execute. Raises GuardError, TimeoutError or sqlite3.Error on failure."""
    check_sql(sql)
    # Defaults read at call time (not import time) so the benchmark can change config.
    return _execute(db_path, sql, timeout_s or config.QUERY_TIMEOUT_S, max_rows or config.MAX_ROWS)


def _execute(db_path, sql, timeout_s, max_rows) -> QueryResult:
    # Split out from run_query so tests can prove the engine layers block writes on their own.
    with closing(open_readonly(db_path)) as conn:
        conn.set_authorizer(_authorizer)
        # sqlite3's connect(timeout=) only covers waiting for locks, not slow queries.
        # A progress handler runs every N VM steps; returning True aborts the query.
        deadline = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            cur = conn.execute(sql)
            # Fetch one extra row: cheap way to know the result was cut off,
            # without rewriting the user's SQL to add a LIMIT.
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as e:
            if str(e) == "interrupted":
                raise TimeoutError(f"Query took longer than {timeout_s}s and was stopped.") from e
            raise
        columns = [d[0] for d in cur.description]
    return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)
