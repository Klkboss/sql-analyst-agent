import io
import sqlite3
from contextlib import closing

import pandas as pd
import pytest

from agent.ingest import build_db, clean_name, original_names
from agent.sandbox import run_query


@pytest.mark.parametrize("raw, expected", [
    ("Order Date", "order_date"), ("unitPrice", "unit_price"), ("Preço (R$)", "preco_r"),
    ("2023 sales", "c_2023_sales"), ("  ", "col"), ("ID", "id"),
])
def test_clean_name(raw, expected):
    assert clean_name(raw, set()) == expected


def test_clean_name_dedupes():
    taken = set()
    assert [clean_name(n, taken) for n in ["Total", "total", "TOTAL"]] == ["total", "total_2", "total_3"]


def _excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
    return buf.getvalue()


def test_csv_and_excel_become_queryable_tables(tmp_path):
    csv_bytes = "Order ID;Unit Price;Customer Name\n1;9,5;Luís\n2;3;Ana\n".encode("latin-1")  # ';' + latin-1
    xlsx = _excel({"North": pd.DataFrame({"Region Code": ["N1"], "Sales": [10]}),
                   "South": pd.DataFrame({"Region Code": ["S1"], "Sales": [20]})})
    db = tmp_path / "up.sqlite"
    mapping = build_db([("My Orders.csv", csv_bytes), ("regions.xlsx", xlsx)], db)

    assert set(mapping) == {"my_orders", "regions_north", "regions_south"}
    assert mapping["my_orders"]["columns"] == {"order_id": "Order ID", "unit_price": "Unit Price",
                                               "customer_name": "Customer Name"}
    r = run_query(db, "SELECT customer_name FROM my_orders WHERE order_id = 1")
    assert r.rows == [("Luís",)]
    assert run_query(db, "SELECT SUM(unit_price) FROM my_orders").rows == [(12.5,)]  # '9,5' read as 9.5
    assert run_query(db, "SELECT SUM(sales) FROM (SELECT sales FROM regions_north UNION ALL "
                         "SELECT sales FROM regions_south)").rows == [(30,)]


def test_rebuild_replaces_old_tables(tmp_path):
    db = tmp_path / "up.sqlite"
    build_db([("a.csv", b"x\n1\n")], db)
    build_db([("b.csv", b"y\n2\n")], db)
    with closing(sqlite3.connect(db)) as c:
        assert [r[0] for r in c.execute("SELECT name FROM sqlite_master")] == ["b"]


def test_unsupported_file_type(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        build_db([("notes.txt", b"hi")], tmp_path / "x.sqlite")


def test_original_names_maps_back_only_known_columns():
    mapping = {"t": {"source": "t.csv", "columns": {"order_date": "Order Date", "total": "Total"}}}
    assert original_names(["order_date", "n_orders", "total"], mapping) == ["Order Date", "n_orders", "Total"]
