"""Self-check on the SQL text itself: shapes that run fine but usually give a wrong answer.

The result checks in loop.py catch empty/NULL results. These catch two patterns seen on
Olist ("top 10 categories by average review score"), where the query ran and returned
plausible rows that were wrong:

1. A scalar subquery inside a JOIN's ON condition, e.g.
       JOIN order_reviews r ON p.product_id = (SELECT product_id FROM order_items WHERE ...)
   A scalar subquery returns only the FIRST matching row, so orders with several items
   silently lose the rest. The fix is a plain join through the bridge table.
2. Ranking by a computed value that isn't returned, e.g. ORDER BY AVG(score) DESC with
   no AVG(score) in the SELECT list: the user can't see why the rows are in that order,
   and the explanation step then guesses numbers.

Text checks, not a full parser: they look at the outermost query and can miss exotic SQL,
but a false alarm only costs one retry (the model can resubmit the same SQL to confirm).
"""
import re

from agent.sandbox import strip_literals

_CLAUSE_END = re.compile(r"(JOIN|WHERE|GROUP|ORDER|LIMIT|HAVING|UNION|EXCEPT|INTERSECT|WINDOW)\b", re.I)
_AGGREGATE = re.compile(r"\b(AVG|SUM|COUNT|MIN|MAX|TOTAL)\s*\(", re.I)


def _depths(code: str) -> list[int]:
    """Parenthesis depth at each character."""
    out, d = [], 0
    for ch in code:
        if ch == "(":
            d += 1
        out.append(d)
        if ch == ")":
            d -= 1
    return out


def _word_at(code: str, i: int, pattern: re.Pattern) -> re.Match | None:
    """Match `pattern` at i, only at a word start."""
    if i > 0 and (code[i - 1].isalnum() or code[i - 1] == "_"):
        return None
    return pattern.match(code, i)


def scalar_subquery_in_join(code: str) -> bool:
    for on in re.finditer(r"\bON\b", code, re.I):
        depth, end = 0, len(code)
        for i in range(on.end(), len(code)):  # the ON condition ends at the next clause
            if code[i] == "(":
                depth += 1
            elif code[i] == ")":
                if depth == 0:
                    end = i  # closing paren of an enclosing subquery
                    break
                depth -= 1
            elif depth == 0 and _word_at(code, i, _CLAUSE_END):
                end = i
                break
        if re.search(r"\(\s*SELECT\b", code[on.end():end], re.I):
            return True
    return False


def _split_top(text: str) -> list[str]:
    """Split on commas that aren't inside parentheses."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def _norm(expr: str) -> str:
    return re.sub(r"\s+", "", expr).lower()


def hidden_ranking_metric(code: str) -> str | None:
    """The first ORDER BY aggregate of the outermost query that the SELECT list doesn't return."""
    depth = _depths(code)
    top = lambda word: [m for m in re.finditer(rf"\b{word}\b", code, re.I) if depth[m.start()] == 0]  # noqa: E731
    selects, froms, orders = top("SELECT"), top("FROM"), top(r"ORDER\s+BY")
    if not selects or not orders:
        return None
    sel = selects[-1]  # the main SELECT (CTE bodies sit inside parentheses)
    frm = next((f for f in froms if f.start() > sel.end()), None)
    select_list = _norm(code[sel.end(): frm.start() if frm else len(code)])
    order = orders[-1]
    limit = next((m for m in top("LIMIT") if m.start() > order.end()), None)
    for item in _split_top(code[order.end(): limit.start() if limit else len(code)]):
        expr = re.sub(r"\s+(ASC|DESC)\b.*$|\s+NULLS\s+(FIRST|LAST)\b.*$", "", item.strip(), flags=re.I)
        expr = expr.rstrip(";").strip()
        if _AGGREGATE.search(expr) and _norm(expr) not in select_list:
            return expr
    return None


# "Show first name and last name": the user listed the columns, so an extra metric column is
# unwanted (and fails Chinook's check). shortcut: verb + "and"/comma heuristic; replace with an
# LLM judgement if it misfires on real questions.
_LISTS_COLUMNS = re.compile(r"\b(?:show|list|return|give|display|provide|state)\b[^.?!]*(?:\band\b|,)", re.I)


def shape_problem(sql: str, question: str = "") -> str | None:
    """Feedback for the model if the SQL has a known bad shape, else None."""
    code = strip_literals(sql)
    if scalar_subquery_in_join(code):
        return ("The query uses a scalar subquery (SELECT ...) inside a JOIN's ON condition. A "
                "scalar subquery returns only the first matching row, so rows are silently lost "
                "when there are several matches. Join the tables directly instead, through the "
                "bridge table listed under 'Join paths' in the schema.")
    if not _LISTS_COLUMNS.search(question) and (metric := hidden_ranking_metric(code)):
        return (f"The query ranks rows by {metric} but doesn't return it. Add it to the SELECT "
                "list with a clear alias so the user can see the values. If the question asked "
                "for specific columns only, return the same query unchanged.")
    return None


# --- repair ---------------------------------------------------------------------------

_TABLE_ALIAS = re.compile(r'\b(?:FROM|JOIN)\s+(?:"[^"]+"|\w+)\s+(?:AS\s+)?(\w+)\b', re.I)


def fix_keyword_aliases(sql: str) -> tuple[str, list[str]]:
    """Rename table aliases that are SQL keywords: `FROM order_reviews or` -> `or_`.

    On Olist the 4B model aliased order_reviews as "or" and kept doing it through three
    retries even when told why it failed, so this is repaired, not just reported.
    Only words also used as a qualifier (`or.col`) count as aliases, so the WHERE in
    `FROM orders WHERE ...` is never touched.
    """
    from agent.schema import _needs_quotes  # the SQLite keyword probe
    renamed = []
    for m in reversed(list(_TABLE_ALIAS.finditer(sql))):
        word = m.group(1)
        if word.lower() in {w.lower() for w in renamed} or not _needs_quotes(word):
            continue
        if not re.search(rf"\b{re.escape(word)}\.", sql, re.I):
            continue  # a clause keyword after the table name, not an alias
        sql = sql[:m.start(1)] + word + "_" + sql[m.end(1):]
        renamed.append(word)
    for word in renamed:
        sql = re.sub(rf"\b{re.escape(word)}\.", f"{word}_.", sql, flags=re.I)
    return sql, renamed
