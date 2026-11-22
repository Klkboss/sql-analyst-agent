"""Describe a SQLite database as compact text for the LLM prompt.

For each table: columns with types, primary/foreign keys, and 3 sample rows.
Sample rows matter: they show the model real value formats (e.g. dates as
'2009-01-01 00:00:00', country names spelled 'USA' not 'United States').
Foreign keys tell the model how to join tables, which matters most on multi-join questions.
"""
from contextlib import closing
from pathlib import Path

from agent.sandbox import open_readonly

SAMPLE_ROWS = 3
MAX_VALUE_CHARS = 50  # long text in sample rows wastes prompt tokens without helping


def _q(name: str) -> str:
    """Quote an identifier so names with spaces or quotes (e.g. from CSVs) still work."""
    return '"' + name.replace('"', '""') + '"'


def _short(value) -> str:
    s = "NULL" if value is None else str(value)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "..."


def get_schema(db_path: str | Path) -> str:
    with closing(open_readonly(db_path)) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")]
        parts = []
        for t in tables:
            # pragma_*() table functions accept the table name as a bound parameter.
            fks = {frm: f"{ref}.{to}" for frm, ref, to in conn.execute(
                'SELECT "from", "table", "to" FROM pragma_foreign_key_list(?)', (t,))}
            lines = [f"Table {t}:"]
            for col, ctype, pk in conn.execute(
                    "SELECT name, type, pk FROM pragma_table_info(?) ORDER BY cid", (t,)):
                extra = " PRIMARY KEY" if pk else ""
                if col in fks:
                    extra += f" REFERENCES {fks[col]}"
                lines.append(f"  {col} {ctype or 'ANY'}{extra}")
            # Table names can't be bound as parameters; t comes from sqlite_master and _q() escapes it.
            cur = conn.execute(f"SELECT * FROM {_q(t)} LIMIT ?", (SAMPLE_ROWS,))  # nosemgrep
            lines.append("  Sample rows (" + " | ".join(d[0] for d in cur.description) + "):")
            lines += ["    " + " | ".join(_short(v) for v in row) for row in cur]
            parts.append("\n".join(lines))
    return "\n\n".join(parts)
