"""Describe a SQLite database as compact text for the LLM prompt.

Two levels of detail (the benchmark compares them):
  "names" - table and column names only (the baseline)
  "full"  - plus types, primary/foreign keys and 3 sample rows per table
Sample rows show the model real value formats (e.g. dates as '2009-01-01 00:00:00',
'USA' not 'United States'). Foreign keys tell it how to join tables.
"""
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from agent.sandbox import open_readonly

SAMPLE_ROWS = 3
MAX_VALUE_CHARS = 50  # long text in sample rows wastes prompt tokens without helping


def _q(name: str) -> str:
    """Quote an identifier so names with spaces or quotes (e.g. from CSVs) still work."""
    return '"' + name.replace('"', '""') + '"'


@lru_cache(maxsize=None)
def _needs_quotes(name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z_]\w*", name):
        return True  # spaces, symbols, leading digit...
    # Keywords like ORDER break bare SQL (BIRD's financial.order table did). Rather than keep a
    # keyword list, ask SQLite whether the bare name parses as a table and column name.
    # Safe to format: the regex above allows only letters, digits and underscores.
    try:
        with closing(sqlite3.connect(":memory:")) as c:
            c.execute(f'WITH {name} AS (SELECT 1 AS "{name}") SELECT {name} FROM {name}')  # nosemgrep
        return False
    except sqlite3.Error:
        return True


def _show(name: str) -> str:
    """Name as the model should write it: bare if SQLite accepts it bare, else quoted."""
    return _q(name) if _needs_quotes(name) else name


def _short(value) -> str:
    s = "NULL" if value is None else str(value)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "..."


def list_tables(db_path: str | Path) -> list[str]:
    with closing(open_readonly(db_path)) as conn:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")]


# Cached: the benchmark asks for the same schema hundreds of times.
@lru_cache(maxsize=64)
def get_schema(db_path: str | Path, detail: str = "full", tables: tuple[str, ...] | None = None) -> str:
    """Schema text for all tables, or only `tables` (used after table selection)."""
    names = [t for t in list_tables(db_path) if tables is None or t in tables]
    with closing(open_readonly(db_path)) as conn:
        parts = []
        for t in names:
            cols = conn.execute("SELECT name, type, pk FROM pragma_table_info(?) ORDER BY cid", (t,)).fetchall()
            if detail == "names":
                parts.append(f"Table {_show(t)}: " + ", ".join(_show(c) for c, _, _ in cols))
                continue
            # pragma_*() table functions accept the table name as a bound parameter.
            # `to` is NULL for "REFERENCES t" with no column, which means t's primary key.
            fks = {frm: f"{_show(ref)}.{_show(to)}" if to else _show(ref) for frm, ref, to in conn.execute(
                'SELECT "from", "table", "to" FROM pragma_foreign_key_list(?)', (t,))}
            lines = [f"Table {_show(t)}:"]
            for col, ctype, pk in cols:
                extra = " PRIMARY KEY" if pk else ""
                if col in fks:
                    extra += f" REFERENCES {fks[col]}"
                lines.append(f"  {_show(col)} {ctype or 'ANY'}{extra}")
            # Table names can't be bound as parameters; t comes from sqlite_master and _q() escapes it.
            cur = conn.execute(f"SELECT * FROM {_q(t)} LIMIT ?", (SAMPLE_ROWS,))  # nosemgrep
            lines.append("  Sample rows (" + " | ".join(d[0] for d in cur.description) + "):")
            lines += ["    " + " | ".join(_short(v) for v in row) for row in cur]
            parts.append("\n".join(lines))
    sep = "\n" if detail == "names" else "\n\n"
    return sep.join(parts)
