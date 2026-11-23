"""Join fan-out check: COUNT/SUM over rows that a one-to-many join has duplicated.

Olist: "How many boleto orders contained a bed/bath product?" was answered with COUNT(*)
over orders JOIN order_payments JOIN order_items. An order with 3 items appears 3 times,
so the count was 1,875 instead of 1,601. Nothing errors; the number is just too big.

How the check decides, for the outermost query:
1. Read the tables (and aliases) in FROM/JOIN and the `a.x = b.y` join conditions.
2. Joining A to B on B.y repeats A's rows when B.y is NOT unique in B (several B rows per
   value). Uniqueness is read from the data, so it works the same for declared foreign keys,
   shared keys and uploaded CSVs with no keys at all.
3. A table is "multiplied" if any join reachable from it repeats its rows.
4. Flag SUM(x) / COUNT(x) when x comes from a multiplied table, and COUNT(*) when every
   table is multiplied (so the rows counted are none of the entities, just combinations).
   COUNT(DISTINCT ...) is never flagged.

Conservative on purpose: CTEs, derived tables, USING joins or unknown names skip the check,
because a false alarm costs a retry and can talk the model out of a correct query.
"""
import re
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from agent.sandbox import _execute, open_readonly
from agent.schema import _q, _show, list_tables
from agent.sqlcheck import _depths

# Only comments and 'string literals' are blanked: double-quoted names must survive here.
_STRINGS = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?(?:\*/|$)", re.DOTALL)
_NAME = r'"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+'
_NOT_ALIAS = {"on", "where", "join", "left", "right", "inner", "outer", "cross", "natural", "full",
              "group", "order", "limit", "using", "union", "having", "window", "except", "intersect"}
# The alias part refuses keywords, so `FROM orders JOIN payments` doesn't eat the JOIN
# (and with it the next table) as an alias.
_REF = re.compile(rf"\b(?:FROM|JOIN)\s+({_NAME})(?:\s+(?:AS\s+)?(?!(?:{'|'.join(_NOT_ALIAS)})\b)(\w+))?", re.I)
_EQ = re.compile(rf"\b(\w+)\.({_NAME})\s*=\s*(\w+)\.({_NAME})")
_AGG = re.compile(r"\b(COUNT|SUM)\s*\(", re.I)

FANOUT_RULE = "When counting entities after joins, use COUNT(DISTINCT key): joins to one-to-many tables repeat rows."


def _unquote(name: str) -> str:
    return name[1:-1] if name[:1] in "\"`[" else name


@lru_cache(maxsize=1024)
def _columns(db: str, table: str) -> dict[str, bool]:
    """{column: is it the table's single-column primary key}."""
    with closing(open_readonly(db)) as conn:
        rows = conn.execute("SELECT name, pk FROM pragma_table_info(?)", (table,)).fetchall()
    single_pk = sum(1 for _, pk in rows if pk) == 1
    return {name: bool(pk) and single_pk for name, pk in rows}


@lru_cache(maxsize=1024)
def _unique(db: str, table: str, col: str) -> bool:
    """Is every non-NULL value of table.col different? (Primary keys: yes, without a query.)"""
    if _columns(db, table).get(col):
        return True
    # Names were checked against the schema before we get here, and _q() escapes them.
    sql = f"SELECT COUNT({_q(col)}) = COUNT(DISTINCT {_q(col)}) FROM {_q(table)}"  # nosemgrep
    try:
        return bool(_execute(db, sql, 10, 1).rows[0][0])
    except Exception:  # timeout on a huge table: assume unique, i.e. don't raise an alarm
        return True


def _args(code: str, start: int) -> str:
    """Text inside the parentheses that open at `start`."""
    depth = 0
    for i in range(start, len(code)):
        depth += code[i] == "("
        depth -= code[i] == ")"
        if depth == 0:
            return code[start + 1:i]
    return code[start + 1:]


def fanout_problem(sql: str, db_path: str | Path) -> str | None:
    db = str(db_path)
    code = _STRINGS.sub("''", sql)
    depth = _depths(code)
    selects = [m for m in re.finditer(r"\bSELECT\b", code, re.I) if depth[m.start()] == 0]
    if not selects or re.match(r"\s*WITH\b", code, re.I) or re.search(r"\bUSING\s*\(", code, re.I):
        return None
    main = selects[-1].start()
    real = {t.lower(): t for t in list_tables(db)}

    aliases: dict[str, str] = {}  # alias (lower) -> real table name
    for m in _REF.finditer(code, main):
        if depth[m.start()] != 0:
            continue
        table = real.get(_unquote(m.group(1)).lower())
        if table is None:
            return None  # a CTE, derived table or unknown name: don't guess
        alias = m.group(2) if m.group(2) and m.group(2).lower() not in _NOT_ALIAS else _unquote(m.group(1))
        aliases[alias.lower()] = table
    if re.search(r"\b(?:FROM|JOIN)\s*\(", code[main:], re.I) or len(aliases) < 2:
        return None

    def col_of(alias: str, name: str) -> tuple[str, str] | None:
        table = aliases.get(alias.lower())
        cols = {c.lower(): c for c in _columns(db, table)} if table else {}
        col = cols.get(_unquote(name).lower())
        return (alias.lower(), col) if col else None

    edges = []  # (alias_a, col_a, alias_b, col_b)
    for m in _EQ.finditer(code, main):
        if depth[m.start()] == 0:
            a, b = col_of(m.group(1), m.group(2)), col_of(m.group(3), m.group(4))
            if a and b and a[0] != b[0]:
                edges.append((*a, *b))

    def repeated_by(start: str) -> tuple[str, str] | None:
        """(table, column) whose duplicates multiply `start`'s rows, or None."""
        seen, todo = {start}, [start]
        while todo:
            x = todo.pop()
            for a, ca, b, cb in edges:
                for near, far, far_col in ((a, b, cb), (b, a, ca)):
                    if near == x and far not in seen:
                        if not _unique(db, aliases[far], far_col):
                            return aliases[far], far_col
                        seen.add(far)
                        todo.append(far)
        return None

    why = {a: repeated_by(a) for a in aliases}
    for m in _AGG.finditer(code, main):
        if depth[m.start()] != 0:
            continue
        arg = _args(code, m.end() - 1).strip()
        if arg.upper().startswith("DISTINCT"):
            continue
        func = m.group(1).upper()
        if arg == "*":
            if func == "COUNT" and all(why.values()):
                table, col = next(iter(why.values()))
                keys = [f"{_show(t)}.{_show(c)}" for t in dict.fromkeys(aliases.values())
                        for c, pk in _columns(db, t).items() if pk]
                return (f"COUNT(*) counts duplicated rows: {table}.{col} has several rows per value, "
                        "so the joins repeat rows. Count the entity you mean with COUNT(DISTINCT key)"
                        + (f", e.g. COUNT(DISTINCT {keys[0]})" if keys else "")
                        + ". If counting the joined rows is intended, return the same query unchanged.")
            continue
        used = [a.lower() for a in re.findall(r"\b(\w+)\.", arg) if a.lower() in aliases]
        if not used:  # bare column: find the one table that has it
            names = {w.lower() for w in re.findall(r"\b\w+\b", arg)}
            used = [a for a, t in aliases.items() if names & {c.lower() for c in _columns(db, t)}]
            used = used if len(used) == 1 else []
        for a in used:
            if why[a]:
                table, col = why[a]
                return (f"{func}({arg}) {'counts' if func == 'COUNT' else 'adds up'} rows of "
                        f"{aliases[a]} that the join repeats: {table}.{col} has several rows per value. "
                        + ("Use COUNT(DISTINCT ...) on the key you are counting. " if func == "COUNT" else
                           f"Aggregate {table} in a subquery first, or sum over distinct {aliases[a]} rows. ")
                        + "If the repeats are intended, return the same query unchanged.")
    return None
