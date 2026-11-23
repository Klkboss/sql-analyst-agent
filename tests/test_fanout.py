import sqlite3
from contextlib import closing

import pytest

from agent.fanout import fanout_problem


@pytest.fixture
def shop(tmp_path):
    """orders 1-N items, orders 1-N payments, items N-1 products. Order 1 has 2 items, 2 payments."""
    path = tmp_path / "shop.sqlite"
    with closing(sqlite3.connect(path)) as c:
        c.executescript("""
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE products (product_id INTEGER PRIMARY KEY, category TEXT);
            CREATE TABLE items (order_id INTEGER REFERENCES orders, product_id INTEGER REFERENCES products, price REAL);
            CREATE TABLE payments (order_id INTEGER REFERENCES orders, kind TEXT, value REAL);
            INSERT INTO orders VALUES (1, 'ok'), (2, 'ok');
            INSERT INTO products VALUES (10, 'bed'), (11, 'toys');
            INSERT INTO items VALUES (1, 10, 5.0), (1, 11, 7.0), (2, 10, 5.0);
            INSERT INTO payments VALUES (1, 'card', 8.0), (1, 'voucher', 4.0), (2, 'boleto', 5.0);
        """)
    return path


@pytest.mark.parametrize("sql, fragment", [
    # every table repeated: the count is item x payment combinations (the Olist boleto bug)
    ("SELECT COUNT(*) FROM orders JOIN payments ON orders.order_id = payments.order_id "
     "JOIN items ON orders.order_id = items.order_id", "COUNT(DISTINCT orders.order_id)"),
    # payment values added once per item
    ("SELECT p.category, SUM(pay.value) FROM payments pay JOIN items i ON i.order_id = pay.order_id "
     "JOIN products p ON p.product_id = i.product_id GROUP BY 1", "adds up rows of payments"),
    # counting a repeated table's column without DISTINCT
    ("SELECT COUNT(o.order_id) FROM orders o JOIN items i ON i.order_id = o.order_id", "counts rows of orders"),
])
def test_flags_fanned_out_aggregates(shop, sql, fragment):
    assert fragment in fanout_problem(sql, shop)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(DISTINCT o.order_id) FROM orders o JOIN items i ON i.order_id = o.order_id "
    "JOIN payments p ON p.order_id = o.order_id",                                   # DISTINCT: fine
    "SELECT COUNT(*) FROM orders o JOIN items i ON i.order_id = o.order_id",        # counts items: fine
    "SELECT p.category, SUM(i.price) FROM items i JOIN products p ON p.product_id = i.product_id GROUP BY 1",
    "SELECT AVG(i.price) FROM items i JOIN orders o ON o.order_id = i.order_id",    # AVG isn't checked
    "SELECT COUNT(*) FROM items",                                                   # no join
    "WITH x AS (SELECT order_id FROM items) SELECT COUNT(*) FROM x JOIN orders o ON o.order_id = x.order_id",
    "SELECT COUNT(*) FROM orders o JOIN (SELECT order_id FROM items) t ON t.order_id = o.order_id",
])
def test_leaves_correct_or_unknown_shapes_alone(shop, sql):
    assert fanout_problem(sql, shop) is None


def test_works_without_declared_keys(tmp_path):
    # An uploaded CSV pair: no foreign keys, so "one-to-many" comes from the data itself.
    path = tmp_path / "up.sqlite"
    with closing(sqlite3.connect(path)) as c:
        c.executescript("""
            CREATE TABLE customers (customer_id INT, region TEXT);
            CREATE TABLE sales (customer_id INT, amount REAL);
            INSERT INTO customers VALUES (1, 'N'), (2, 'S');
            INSERT INTO sales VALUES (1, 10), (1, 20), (2, 5);
        """)
    sql = "SELECT c.region, COUNT(c.customer_id) FROM customers c JOIN sales s ON s.customer_id = c.customer_id GROUP BY 1"
    assert "counts rows of customers" in fanout_problem(sql, path)
