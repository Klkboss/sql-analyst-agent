import pandas as pd
import pytest

from agent.charts import MAX_BARS, pick_chart


@pytest.mark.parametrize("df, kind", [
    (pd.DataFrame({"n": [59]}), "metric"),
    (pd.DataFrame({"month": ["2017-01", "2017-02", "2017-03"], "orders": [5, 9, 7]}), "line"),
    (pd.DataFrame({"year": [2016, 2017, 2018], "revenue": [1.5, 2.5, 3.0]}), "line"),  # int years
    (pd.DataFrame({"Order Date": ["a", "b"], "Total": [1, 2]}), "line"),               # name says time
    (pd.DataFrame({"country": ["USA", "Canada"], "sales": [523.1, 303.9]}), "bar"),
    (pd.DataFrame({"price": [1.0, 2.0, 3.0], "weight": [3, 1, 2]}), "scatter"),
    (pd.DataFrame({"price": [1.0, 2.0, 2.5, 9.0]}), "histogram"),
])
def test_picks_expected_chart(df, kind):
    assert pick_chart(df).kind == kind


@pytest.mark.parametrize("df", [
    pd.DataFrame(),                                           # empty result
    pd.DataFrame({"name": ["Iron Maiden"]}),                  # single text value
    pd.DataFrame({"first": ["Ana"], "last": ["Silva"]}),      # text only
])
def test_table_only(df):
    assert pick_chart(df) is None


def test_iso_dates_detected_by_value_not_just_name():
    df = pd.DataFrame({"d": ["2017-01-05", "2017-02-05", "2017-03-05"], "n": [1, 3, 2]})
    assert pick_chart(df).kind == "line"


def test_candidate_is_not_mistaken_for_a_date():
    df = pd.DataFrame({"candidate": ["a", "b"], "votes": [3, 4]})
    assert pick_chart(df).kind == "bar"


def test_bar_keeps_first_rows_and_says_so():
    df = pd.DataFrame({"genre": [f"g{i}" for i in range(40)], "tracks": range(40)})
    c = pick_chart(df)
    assert c.kind == "bar" and str(MAX_BARS) in c.note
    assert len(c.figure.data[0].x) == MAX_BARS


def test_line_splits_by_small_label_column():
    df = pd.DataFrame({"year": [2017, 2017, 2018, 2018], "country": ["BR", "US"] * 2, "sales": [1, 2, 3, 4]})
    assert len(pick_chart(df).figure.data) == 2  # one line per country
