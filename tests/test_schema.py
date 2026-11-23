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
