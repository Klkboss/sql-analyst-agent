"""Shared pytest fixture: a tiny throwaway SQLite DB, so tests need neither Chinook nor an LLM."""
import sqlite3
from contextlib import closing

import pytest


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE artist (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE album (
                id INTEGER PRIMARY KEY,
                title TEXT,
                artist_id INTEGER REFERENCES artist(id)
            );
            CREATE TABLE "order items" ("item id" INTEGER PRIMARY KEY, "unit price" REAL,
                                        album_id INTEGER REFERENCES album);  -- FK with no column
            INSERT INTO artist VALUES (1, 'AC/DC'), (2, 'Accept'), (3, 'Aerosmith'), (4, 'Audioslave');
            INSERT INTO album VALUES (1, 'For Those About To Rock', 1),
                                     (2, 'Balls to the Wall', 2),
                                     (3, '""" + "A" * 80 + """', 3);
        """)
        conn.commit()
    return path
