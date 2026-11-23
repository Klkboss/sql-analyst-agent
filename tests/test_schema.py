from agent.schema import MAX_VALUE_CHARS, get_schema


def test_lists_tables_columns_and_types(db):
    s = get_schema(db)
    assert "Table album:" in s and "Table artist:" in s
    assert "  name TEXT" in s
    assert "  id INTEGER PRIMARY KEY" in s


def test_shows_foreign_keys(db):
    s = get_schema(db)
    assert "artist_id INTEGER REFERENCES artist.id" in s
    assert "album_id INTEGER REFERENCES album\n" in s  # FK that names no column


def test_at_most_three_sample_rows(db):
    artist_block = get_schema(db).split("Table artist:")[1]
    assert "AC/DC" in artist_block and "Aerosmith" in artist_block
    assert "Audioslave" not in artist_block  # 4th row is left out


def test_names_only_mode(db):
    s = get_schema(db, "names")
    assert "Table artist: id, name" in s
    assert "INTEGER" not in s and "AC/DC" not in s


def test_table_subset(db):
    s = get_schema(db, "full", ("artist",))
    assert "Table artist:" in s and "Table album:" not in s


def test_odd_names_are_quoted(db):
    assert 'Table "order items": "item id", "unit price", album_id' in get_schema(db, "names")


def test_keywords_are_quoted_plain_names_are_not():
    from agent.schema import _show
    assert _show("order") == '"order"' and _show("group") == '"group"' and _show("SELECT") == '"SELECT"'
    assert _show("name") == "name" and _show("order_id") == "order_id"
    assert _show("key") == "key"  # a keyword SQLite still accepts as a plain name


def test_long_values_are_shortened(db):
    s = get_schema(db)
    assert "A" * MAX_VALUE_CHARS + "..." in s
    assert "A" * (MAX_VALUE_CHARS + 1) not in s


# --- join paths -----------------------------------------------------------------------

def _make(tmp_path, ddl):
    import sqlite3
    from contextlib import closing
    path = tmp_path / "j.sqlite"
    with closing(sqlite3.connect(path)) as c:
        c.executescript(ddl)
    return path


def test_plain_fk_chain_is_not_repeated_but_still_bridges(db):
    from agent.schema import bridge_tables
    # artist <- album <- "order items" is two REFERENCES lines already: no extra path line...
    assert "<->" not in get_schema(db)
    # ...but if table selection picks only the two ends, the bridge is still added
    assert bridge_tables(db, ["artist", "order items"]) == ["album", "artist", "order items"]


def test_siblings_join_on_shared_parent_key_but_not_self_references(tmp_path):
    path = _make(tmp_path, """
        CREATE TABLE orders (order_id TEXT PRIMARY KEY);
        CREATE TABLE items (order_id TEXT REFERENCES orders, product_id TEXT REFERENCES products);
        CREATE TABLE reviews (order_id TEXT REFERENCES orders(order_id), score INT);
        CREATE TABLE products (product_id TEXT PRIMARY KEY);
        CREATE TABLE staff (id INT PRIMARY KEY, boss_id INT REFERENCES staff(id));
    """)
    s = get_schema(path)
    assert "items, reviews join each other on order_id (all reference orders.order_id)" in s
    assert "products <-> reviews via items (product_id, order_id)" in s   # the Olist case
    assert "boss_id" not in s.split("Joins")[-1]                          # self-reference: no sibling line


def test_uploads_without_keys_join_on_same_named_id_columns(tmp_path):
    path = _make(tmp_path, """
        CREATE TABLE sales (id INT, customer_id INT, amount REAL);
        CREATE TABLE customers (id INT, customer_id INT, name TEXT);
    """)
    s = get_schema(path)
    assert "customers, sales share customer_id (same column name, likely join key)" in s
    assert "share id " not in s  # every table's own "id" is not a link
