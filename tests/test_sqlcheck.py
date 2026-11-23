import pytest

from agent.sqlcheck import fix_keyword_aliases, shape_problem

OLIST_BUG = """SELECT ct.product_category_name_english FROM products p
JOIN category_translation ct ON p.product_category_name = ct.product_category_name
JOIN order_reviews orv ON p.product_id = (SELECT product_id FROM order_items WHERE order_id = orv.order_id)
GROUP BY ct.product_category_name_english ORDER BY AVG(orv.review_score) DESC LIMIT 10"""


def test_flags_scalar_subquery_in_join():
    assert "scalar subquery" in shape_problem(OLIST_BUG)


@pytest.mark.parametrize("sql", [
    "SELECT c, AVG(s) FROM t JOIN (SELECT id, s FROM u) x ON x.id = t.id GROUP BY c",  # derived table: fine
    "SELECT a FROM t JOIN u ON u.id = t.id WHERE t.x = (SELECT MAX(x) FROM t)",         # subquery in WHERE
    "SELECT a FROM t WHERE b IN (SELECT b FROM u JOIN v ON v.id = u.id)",               # ON inside subquery, plain
])
def test_subqueries_elsewhere_are_fine(sql):
    assert shape_problem(sql) is None


def test_flags_ranking_metric_not_returned():
    sql = "SELECT name FROM t GROUP BY name ORDER BY AVG(score) DESC LIMIT 10"
    assert "AVG(score)" in shape_problem(sql)


@pytest.mark.parametrize("sql", [
    "SELECT name, AVG(score) AS avg_score FROM t GROUP BY name ORDER BY avg_score DESC",   # by alias
    "SELECT name, AVG(score) FROM t GROUP BY name ORDER BY AVG(score) DESC",               # same expr
    "SELECT name, AVG( score ) FROM t GROUP BY name ORDER BY avg(score) DESC",             # spacing/case
    "SELECT name FROM t ORDER BY name",                                                    # not computed
    "SELECT name, n FROM (SELECT name, COUNT(*) n FROM t GROUP BY name ORDER BY COUNT(*)) ORDER BY n",
    "WITH x AS (SELECT a FROM t ORDER BY SUM(b)) SELECT a FROM x",                          # inner ORDER BY only
    "SELECT name FROM t WHERE note = 'ORDER BY AVG(x)'",                                   # inside a string
])
def test_ranking_metric_ok(sql):
    assert shape_problem(sql) is None


def test_fix_keyword_aliases():
    sql = "SELECT AVG(OR.score) FROM order_reviews AS OR JOIN items i ON OR.id = i.id"
    fixed, renamed = fix_keyword_aliases(sql)
    assert fixed == "SELECT AVG(OR_.score) FROM order_reviews AS OR_ JOIN items i ON OR_.id = i.id"
    assert renamed == ["OR"]


@pytest.mark.parametrize("sql", [
    "SELECT a FROM orders WHERE x = 1 OR y = 2",              # WHERE / OR are clauses, not aliases
    "SELECT o.a FROM orders o ORDER BY o.a",                  # normal alias
    "SELECT a FROM t JOIN u ON u.id = t.id GROUP BY a",
])
def test_fix_keyword_aliases_leaves_normal_sql_alone(sql):
    assert fix_keyword_aliases(sql) == (sql, [])


@pytest.mark.parametrize("question, flagged", [
    ("Which 10 product categories have the highest average review score?", True),
    ("Show the top 10 categories by average score", True),                   # no columns listed
    ("Which employee sells most? Show first name and last name.", False),    # columns listed
    ("List the name, city of the 3 busiest sellers", False),
])
def test_ranking_check_respects_listed_columns(question, flagged):
    sql = "SELECT name FROM t GROUP BY name ORDER BY AVG(score) DESC LIMIT 10"
    assert (shape_problem(sql, question) is not None) is flagged
