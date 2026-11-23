"""Describe a SQLite database as compact text for the LLM prompt.

Two levels of detail (the benchmark compares them):
  "names" - table and column names only (the baseline)
  "full"  - plus types, primary/foreign keys and 3 sample rows per table
  "lean"  - "full" without sample rows: the last resort when a schema won't fit the context
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


# --- join graph -----------------------------------------------------------------------
# Olist showed the gap: order_reviews and order_items both reference orders.order_id but
# not each other, so the model never saw that reviews reach products via order_items, and
# wrote a scalar-subquery join instead. Listing the paths fixes that.

MAX_JOIN_LINES = 20  # keep big schemas (BIRD) from spending their context budget on paths


@lru_cache(maxsize=64)
def join_graph(db_path: str | Path) -> tuple[dict, list[tuple[frozenset, str]]]:
    """(edges, notes). edges = {frozenset({a, b}): (a, col_a, b, col_b)} for every directly
    joinable pair; notes = (tables, one-line text) for joins the REFERENCES lines don't show."""
    tables = list_tables(db_path)
    by_lower = {t.lower(): t for t in tables}  # REFERENCES may use another letter case
    with closing(open_readonly(db_path)) as conn:
        cols = {t: conn.execute("SELECT name, pk FROM pragma_table_info(?) ORDER BY cid", (t,)).fetchall()
                for t in tables}
        fks = []
        for t in tables:
            for frm, ref, to in conn.execute('SELECT "from", "table", "to" FROM pragma_foreign_key_list(?)', (t,)):
                ref = by_lower.get(ref.lower())
                to = to or next((c for c, pk in cols.get(ref, []) if pk), None)  # bare REFERENCES t = its PK
                if ref and to:
                    fks.append((t, frm, ref, to))

    edges, notes = {}, []

    def link(a, ca, b, cb, declared=False):  # declared = shown as a REFERENCES line
        edges.setdefault(frozenset((a, b)), (a, ca, b, cb, declared))

    for t, frm, ref, to in fks:
        link(t, frm, ref, to, declared=True)
    # Siblings: tables whose same-named column references the same parent key can join on
    # it directly (order_items, order_reviews... on order_id). Self-references
    # (Employee.ReportsTo -> Employee) and differently named columns are left out: joining
    # Customer.SupportRepId to Employee.ReportsTo would be legal SQL but meaningless.
    groups: dict[tuple, list] = {}
    for t, frm, ref, to in fks:
        if t != ref and t not in groups.get((ref, to, frm), []):
            groups.setdefault((ref, to, frm), []).append(t)
    for (ref, to, col), kids in groups.items():
        if len(kids) > 1:
            for i, a in enumerate(kids):
                for b in kids[i + 1:]:
                    link(a, col, b, col)
            notes.append((frozenset(kids), f"{', '.join(_show(t) for t in kids)} join each other on "
                                           f"{_show(col)} (all reference {_show(ref)}.{_show(to)})"))
    # No declared keys at all (CSV uploads): same-named *_id columns are the likely joins.
    if not fks:
        owners: dict[str, list] = {}
        for t, cs in cols.items():
            for c, _ in cs:
                if c.lower().endswith("_id"):  # a bare "id" is every table's own key, not a link
                    owners.setdefault(c, []).append(t)
        for c, ts in owners.items():
            if len(ts) > 1:
                for i, a in enumerate(ts):
                    for b in ts[i + 1:]:
                        link(a, c, b, c)
                notes.append((frozenset(ts), f"{', '.join(_show(t) for t in ts)} share {_show(c)} "
                                             "(same column name, likely join key)"))
    return edges, notes


def _bridges(db_path, tables: list[str]) -> dict[frozenset, str]:
    """{pair: bridge table} for pairs with no direct join but exactly one table between them."""
    edges, _ = join_graph(db_path)
    out = {}
    for i, a in enumerate(tables):
        for b in tables[i + 1:]:
            if frozenset((a, b)) not in edges:
                c = next((c for c in list_tables(db_path) if c not in (a, b)
                          and frozenset((a, c)) in edges and frozenset((c, b)) in edges), None)
                if c:
                    out[frozenset((a, b))] = c
    return out


def bridge_tables(db_path: str | Path, tables: list[str]) -> list[str]:
    """Tables to add so the chosen ones can be joined (used after table selection)."""
    needed = set(_bridges(db_path, tables).values())
    return [t for t in list_tables(db_path) if t in set(tables) | needed]


def _key(edge: tuple, near: str) -> str:
    """Join columns of an edge, compact: 'order_id', or 'SupportRepId=EmployeeId' if they differ."""
    a, ca, b, cb = edge[:4] if edge[0] == near else (edge[2], edge[3], edge[0], edge[1])
    return _show(ca) if ca == cb else f"{_show(ca)}={_show(cb)}"


def _join_lines(db_path, names: list[str]) -> list[str]:
    edges, notes = join_graph(db_path)
    inside = set(names)
    direct = [f"  {text}" for tables, text in notes if tables <= inside]
    paths = []
    for pair, c in _bridges(db_path, names).items():
        a, b = sorted(pair)
        # A path of two REFERENCES edges (customers -> orders -> order_items) is already
        # readable from the lines above; list only paths that need a join the REFERENCES
        # lines don't show (order_reviews -> order_items is a sibling join). Saves tokens.
        hidden = not (edges[frozenset((a, c))][4] and edges[frozenset((c, b))][4])
        if c in inside and hidden:
            paths.append(f"  {_show(a)} <-> {_show(b)} via {_show(c)} "
                         f"({_key(edges[frozenset((a, c))], a)}, {_key(edges[frozenset((c, b))], c)})")
    lines = []
    if direct:
        lines += ["Joins not shown as REFERENCES above:"] + direct
    if paths:
        lines += ["Join paths through a bridge table (join these instead of using subqueries):"] + sorted(paths)
    if len(lines) > MAX_JOIN_LINES:
        lines = lines[:MAX_JOIN_LINES] + [f"  ... {len(lines) - MAX_JOIN_LINES} more not shown"]
    return lines


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
            if detail != "lean":
                # Table names can't be bound as parameters; t comes from sqlite_master and _q() escapes it.
                cur = conn.execute(f"SELECT * FROM {_q(t)} LIMIT ?", (SAMPLE_ROWS,))  # nosemgrep
                lines.append("  Sample rows (" + " | ".join(d[0] for d in cur.description) + "):")
                lines += ["    " + " | ".join(_short(v) for v in row) for row in cur]
            parts.append("\n".join(lines))
    if detail == "names":
        return "\n".join(parts)
    joins = _join_lines(db_path, names)
    return "\n\n".join(parts + (["\n".join(joins)] if joins else []))
