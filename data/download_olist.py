"""Load the Olist Brazilian E-Commerce dataset (Kaggle) into data/olist.sqlite.

Kaggle needs a login, so download it by hand from
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and point this script
at the zip or at the folder you extracted it to:

    python data/download_olist.py              # looks for ./archive.zip or ./archive/
    python data/download_olist.py path/to/archive.zip

Dataset by Olist, licensed CC BY-NC-SA 4.0 (non-commercial use, share-alike).

Design decision: CSVs carry no foreign keys, but the schema's REFERENCES lines are what
tell the model how to join tables. So tables are created from explicit DDL with primary
and foreign keys, then filled; FK columns are indexed so joins on 100k orders stay fast.
"""
import io
import re
import sqlite3
import sys
import zipfile
from contextlib import closing
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DEST = Path(__file__).parent / "olist.sqlite"

# Kaggle file name -> table name
FILES = {
    "olist_customers_dataset.csv": "customers",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_orders_dataset.csv": "orders",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "product_category_name_translation.csv": "category_translation",
}
PRIMARY_KEYS = {"customers": "customer_id", "orders": "order_id", "products": "product_id",
                "sellers": "seller_id", "category_translation": "product_category_name"}
# table -> {column: "ref_table(ref_column)"}
FOREIGN_KEYS = {
    "orders": {"customer_id": "customers(customer_id)"},
    "order_items": {"order_id": "orders(order_id)", "product_id": "products(product_id)",
                    "seller_id": "sellers(seller_id)"},
    "order_payments": {"order_id": "orders(order_id)"},
    "order_reviews": {"order_id": "orders(order_id)"},
    "products": {"product_category_name": "category_translation(product_category_name)"},
}
EXTRA_INDEXES = {"customers": ["customer_zip_code_prefix"], "sellers": ["seller_zip_code_prefix"],
                 "geolocation": ["geolocation_zip_code_prefix"]}


def _reader(src: Path):
    """name -> CSV bytes, from a folder or a zip (members read by name, never extracted)."""
    if src.is_dir():
        return lambda name: (src / name).read_bytes()
    z = zipfile.ZipFile(src)
    by_base = {Path(n).name: n for n in z.namelist()}  # the zip may nest files in a folder
    return lambda name: z.read(by_base[name])


def _sql_type(dtype) -> str:
    if pd.api.types.is_integer_dtype(dtype):
        return "INTEGER"
    return "REAL" if pd.api.types.is_float_dtype(dtype) else "TEXT"


_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def _create_table(conn, table: str, df: pd.DataFrame) -> None:
    # DDL can't use bound parameters, and the column names come from a downloaded CSV:
    # accept only plain snake_case names so nothing in a header can become SQL.
    bad = [c for c in df.columns if not _IDENT.fullmatch(str(c))]
    if bad:
        raise ValueError(f"Unexpected column names in {table}: {bad}")
    fks, pk = FOREIGN_KEYS.get(table, {}), PRIMARY_KEYS.get(table)
    cols = [f"{c} {_sql_type(df[c].dtype)}" + (" PRIMARY KEY" if c == pk else "")
            + (f" REFERENCES {fks[c]}" if c in fks else "") for c in df.columns]
    conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")  # nosemgrep
    for col in list(fks) + EXTRA_INDEXES.get(table, []):
        conn.execute(f"CREATE INDEX idx_{table}_{col} ON {table}({col})")  # nosemgrep


def main() -> None:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        src = next((p for p in (ROOT / "archive.zip", ROOT / "archive") if p.exists()), None)
        if src is None:
            sys.exit("No archive.zip or archive/ folder found. Download Olist from Kaggle and pass its path.")
    try:
        missing = [n for n in FILES if n not in _list(src)]
    except zipfile.BadZipFile:
        sys.exit(f"{src} is not a valid zip file.")
    if missing:
        sys.exit(f"{src} is missing: {', '.join(missing)}")
    read = _reader(src)

    tmp = DEST.with_suffix(".part")
    tmp.unlink(missing_ok=True)
    with closing(sqlite3.connect(tmp)) as conn:
        for name, table in FILES.items():
            raw = read(name)
            header = raw[:2000].decode("utf-8-sig").splitlines()[0]
            # Zip prefixes like "01037" must stay text, or the leading zero is lost.
            zips = {c.strip('"') for c in header.split(",") if c.strip('"').endswith("zip_code_prefix")}
            df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig", dtype={c: str for c in zips})
            _create_table(conn, table, df)
            df.to_sql(table, conn, if_exists="append", index=False, chunksize=50_000)
            print(f"  {table:22} {len(df):>9,} rows")
        conn.commit()
    tmp.replace(DEST)
    print(f"Saved {DEST} ({DEST.stat().st_size / 1e6:.0f} MB)")


def _list(src: Path) -> set[str]:
    if src.is_dir():
        return {p.name for p in src.iterdir()}
    with zipfile.ZipFile(src) as z:
        return {Path(n).name for n in z.namelist()}


if __name__ == "__main__":
    main()
