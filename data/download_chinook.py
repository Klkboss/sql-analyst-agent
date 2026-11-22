"""Download the Chinook sample database (a digital music store) into data/.

Standard library only, so it runs before any pip install.
Usage: python data/download_chinook.py
"""
import sqlite3
import urllib.request
from contextlib import closing
from pathlib import Path

URL = "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite"
DEST = Path(__file__).parent / "chinook.sqlite"


def main() -> None:
    if DEST.exists():
        print(f"Already present: {DEST}")
        return
    print(f"Downloading {URL} ...")
    tmp = DEST.with_suffix(".part")
    urllib.request.urlretrieve(URL, tmp)
    # Check the file really is a SQLite DB before renaming it into place.
    # closing(): sqlite3's own `with` only commits, it doesn't close the file.
    with closing(sqlite3.connect(tmp)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    tmp.replace(DEST)
    print(f"Saved {DEST} ({n} tables)")


if __name__ == "__main__":
    main()
