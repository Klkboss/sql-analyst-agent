"""Turn uploaded CSV / Excel files into a SQLite file the agent can query.

One table per CSV file, or per Excel sheet. Table and column names are cleaned to
snake_case ("Order Date" -> order_date): on BIRD, `no such column` was a top failure
and odd names need quoting, so plain names give the model fewer ways to slip.
The original names are kept in the returned mapping so the UI can show them back.

Our code writes this file; the agent still reads it through the read-only sandbox.
"""
import csv
import io
import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

import pandas as pd


def clean_name(raw, taken: set[str]) -> str:
    """'Order Date' -> 'order_date', 'unitPrice' -> 'unit_price', 'Preço' -> 'preco'. Unique within `taken`."""
    s = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode()
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s.strip())  # split camelCase
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower() or "col"
    if s[0].isdigit():
        s = "c_" + s  # identifiers can't start with a digit
    base, n = s, 2
    while s in taken:
        s, n = f"{base}_{n}", n + 1
    taken.add(s)
    return s


def _read_csv(data: bytes) -> pd.DataFrame:
    sample = data[:20_000].decode("utf-8", errors="ignore")
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter  # ';' is common in European exports
    except csv.Error:
        sep = ","
    decimal = "," if sep == ";" else "."  # ';'-separated files almost always use decimal commas
    try:
        return pd.read_csv(io.BytesIO(data), sep=sep, decimal=decimal, encoding="utf-8")
    except UnicodeDecodeError:  # older Excel CSV exports are often cp1252 / latin-1
        return pd.read_csv(io.BytesIO(data), sep=sep, decimal=decimal, encoding="latin-1")


def read_files(files: list[tuple[str, bytes]]) -> list[tuple[str, pd.DataFrame]]:
    """(filename, bytes) -> [(source label, DataFrame)]: one entry per CSV or non-empty sheet."""
    frames = []
    for name, data in files:
        ext = Path(name).suffix.lower()
        if ext == ".csv":
            frames.append((name, _read_csv(data)))
        elif ext in (".xlsx", ".xls"):
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
            for sheet, df in sheets.items():
                if not df.empty:
                    frames.append((f"{name} / {sheet}" if len(sheets) > 1 else name, df))
        else:
            raise ValueError(f"Unsupported file type: {name} (use .csv, .xlsx or .xls)")
    return frames


def build_db(files: list[tuple[str, bytes]], db_path: str | Path) -> dict[str, dict]:
    """Write every table to a fresh SQLite file. Returns {table: {"source": label, "columns": {clean: original}}}."""
    db_path = Path(db_path)
    db_path.unlink(missing_ok=True)
    tables, mapping = set(), {}
    with closing(sqlite3.connect(db_path)) as conn:
        for source, df in read_files(files):
            label = Path(source.split(" / ")[0]).stem + (f"_{source.split(' / ')[1]}" if " / " in source else "")
            table = clean_name(label, tables)
            taken: set[str] = set()
            cols = {clean_name(c, taken): str(c) for c in df.columns}
            df.columns = list(cols)
            df.to_sql(table, conn, index=False)
            mapping[table] = {"source": source, "columns": cols}
        conn.commit()
    return mapping


def original_names(columns: list[str], mapping: dict[str, dict]) -> list[str]:
    """Result columns -> the user's own column names where we renamed them (first table wins on clashes)."""
    lookup: dict[str, str] = {}
    for info in mapping.values():
        for clean, orig in info["columns"].items():
            lookup.setdefault(clean, orig)
    return [lookup.get(c, c) for c in columns]
