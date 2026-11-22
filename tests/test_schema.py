from agent.schema import MAX_VALUE_CHARS, get_schema


def test_lists_tables_columns_and_types(db):
    s = get_schema(db)
    assert "Table album:" in s and "Table artist:" in s
    assert "  name TEXT" in s
    assert "  id INTEGER PRIMARY KEY" in s


def test_shows_foreign_keys(db):
    assert "artist_id INTEGER REFERENCES artist.id" in get_schema(db)


def test_at_most_three_sample_rows(db):
    artist_block = get_schema(db).split("Table artist:")[1]
    assert "AC/DC" in artist_block and "Aerosmith" in artist_block
    assert "Audioslave" not in artist_block  # 4th row is left out


def test_long_values_are_shortened(db):
    s = get_schema(db)
    assert "A" * MAX_VALUE_CHARS + "..." in s
    assert "A" * (MAX_VALUE_CHARS + 1) not in s
