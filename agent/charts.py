"""Pick a chart for a query result with simple rules (no LLM call).

Design decision: rules instead of asking the LLM. They are instant, deterministic,
easy to explain, and a wrong guess only costs a less useful picture, never a wrong number.

    1 row x 1 number          -> "metric" (big number, no figure)
    time column + number(s)   -> line   (time = ISO date strings or a date/year/month... name)
    label column + number     -> bar    (first 25 rows, in the SQL's order)
    2+ numbers, no labels     -> scatter
    1 number column           -> histogram
    anything else             -> None (table only)
"""
import re
from dataclasses import dataclass

import pandas as pd
import plotly.express as px

MAX_BARS = 25
_TIME_WORDS = {"date", "time", "timestamp", "datetime", "year", "month", "week", "day", "quarter", "period"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}")  # what SQLite's date functions return: 2017-03, 2017-03-05 ...


@dataclass
class Chart:
    kind: str          # metric | line | bar | scatter | histogram
    figure: object = None
    note: str = ""


def _is_time(s: pd.Series) -> bool:
    words = set(re.split(r"[^a-z]+", str(s.name).lower()))  # 'Order Date' -> {order, date}; 'candidate' stays whole
    if words & _TIME_WORDS:
        return True
    # is_string_dtype covers both object columns and pandas 3's dedicated string dtype.
    if pd.api.types.is_string_dtype(s) or s.dtype == object:
        return s.astype(str).str.match(_ISO_DATE).mean() > 0.9
    return False


def _is_number(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def pick_chart(df: pd.DataFrame) -> Chart | None:
    if df.empty:
        return None
    nums = [c for c in df.columns if _is_number(df[c])]
    if df.shape == (1, 1) and nums:
        return Chart("metric")
    time = next((c for c in df.columns if _is_time(df[c])), None)
    ys = [c for c in nums if c != time]
    labels = [c for c in df.columns if c not in nums and c != time]

    if time is not None and ys and len(df) > 1:
        # A small label column becomes one line per label (e.g. sales per month per country).
        color = labels[0] if labels and df[labels[0]].nunique() <= 10 else None
        y = ys[0] if color else ys[:3]
        return Chart("line", px.line(df.sort_values(time), x=time, y=y, color=color, markers=True))

    if labels and ys:
        d, label, value = df.head(MAX_BARS), labels[0], ys[0]
        note = f"Showing the first {MAX_BARS} of {len(df)} rows." if len(df) > MAX_BARS else ""
        if d[label].astype(str).str.len().max() > 15:  # long labels read better on a horizontal bar
            fig = px.bar(d, x=value, y=label, orientation="h")
            fig.update_yaxes(autorange="reversed")  # keep the SQL's order top to bottom
        else:
            fig = px.bar(d, x=label, y=value)
        return Chart("bar", fig, note)

    if len(nums) >= 2 and not labels and len(df) > 1:
        return Chart("scatter", px.scatter(df, x=nums[0], y=nums[1]))
    if len(nums) == 1 and len(df.columns) == 1 and len(df) > 1:
        return Chart("histogram", px.histogram(df, x=nums[0]))
    return None
