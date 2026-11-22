import sqlite3
from contextlib import closing

import pytest

from agent.sandbox import GuardError, _execute, check_sql, run_query


# --- SQL guard -------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from artist;",
    "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
    "SELECT 'DROP TABLE artist' AS s",            # keyword inside a string is fine
    "SELECT 1 -- then DELETE everything",          # keyword inside a comment is fine
    "SELECT REPLACE(name, 'A', 'a') FROM artist",  # REPLACE() the function is allowed
    "  /* leading comment */ SELECT 1 ;  ",
])
def test_guard_allows_reads(sql):
    check_sql(sql)  # must not raise


@pytest.mark.parametrize("sql", [
    "",
    "-- only a comment",
    "DROP TABLE artist",
    "INSERT INTO artist VALUES (9, 'x')",
    "UPDATE artist SET name = 'x'",
    "REPLACE INTO artist VALUES (1, 'x')",
    "PRAGMA table_info(artist)",
    "ATTACH DATABASE 'other.db' AS other",
    "SELECT 1; DROP TABLE artist",                     # stacked statements
    "SELECT 1; /* sneaky */ DELETE FROM artist",
    "/* SELECT */ DELETE FROM artist",                 # comment can't fake the first keyword
    "WITH x AS (SELECT 1) DELETE FROM artist",         # CTE followed by a write
])
def test_guard_rejects_everything_else(sql):
    with pytest.raises(GuardError):
        check_sql(sql)


# --- Sandbox execution ------------------------------------------------------------

def test_returns_columns_and_rows(db):
    r = run_query(db, "SELECT name FROM artist ORDER BY id LIMIT 2")
    assert r.columns == ["name"]
    assert r.rows == [("AC/DC",), ("Accept",)]
    assert not r.truncated


def test_row_limit(db):
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 50) SELECT x FROM c"
    r = run_query(db, sql, max_rows=10)
    assert len(r.rows) == 10 and r.truncated


def test_timeout_stops_runaway_query(db):
    endless = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(TimeoutError):
        run_query(db, endless, timeout_s=0.5)


def test_engine_blocks_writes_even_without_guard(db):
    # Skip the text guard on purpose: the read-only connection + authorizer must still refuse.
    for sql in ["DELETE FROM artist", "DROP TABLE artist", "INSERT INTO artist VALUES (9, 'x')"]:
        with pytest.raises(sqlite3.DatabaseError):
            _execute(db, sql, timeout_s=5, max_rows=10)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 4


def test_missing_db_is_not_created(tmp_path):
    missing = tmp_path / "nope.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        run_query(missing, "SELECT 1")
    assert not missing.exists()
