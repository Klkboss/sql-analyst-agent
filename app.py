"""Streamlit chat UI for the SQL analyst agent.

Run: streamlit run app.py

Each answer shows: a short explanation, a chart (picked by rules in agent/charts.py),
the result table, the SQL, and the log of every attempt. Follow-up questions see the
last few questions and their SQL. "Data notes" are hints added to every question,
passed exactly like BIRD's evidence hints (worth ~16 points in our benchmark).
"""
import hashlib
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from agent import config
from agent.charts import pick_chart
from agent.explain import explain
from agent.ingest import build_db, original_names
from agent.loop import MAX_HISTORY_TURNS, run_agent
from agent.schema import get_schema

ROOT = Path(__file__).parent
SOURCES = {
    "Chinook (music store)": ROOT / "data" / "chinook.sqlite",
    "Olist (Brazilian e-commerce)": ROOT / "data" / "olist.sqlite",
}
SETUP_HINT = {"Chinook (music store)": "python data/download_chinook.py",
              "Olist (Brazilian e-commerce)": "python data/download_olist.py"}
# Built-in data notes, pre-filled in the sidebar (users can edit them). On Olist, without
# the join path the model joined reviews to products with a scalar subquery and lost rows.
DEFAULT_NOTES = {
    "Olist (Brazilian e-commerce)": (
        "Join paths: order_reviews -> order_items (order_id) -> products (product_id) or "
        "sellers (seller_id); order_payments joins the same way; orders -> customers "
        "(customer_id); products -> category_translation (product_category_name), English "
        "names in product_category_name_english.\n"
        "One order can have several items, payments and reviews: count orders with "
        "COUNT(DISTINCT order_id). geolocation has many rows per zip prefix: aggregate it "
        "before joining. Prices are in BRL."),
}
UPLOAD = "Upload CSV / Excel"
UPLOAD_DIR = Path(tempfile.gettempdir()) / "sql-analyst-agent"

st.set_page_config(page_title="SQL Analyst Agent", page_icon="🔎", layout="wide")
state = st.session_state
state.setdefault("chats", {})          # data source -> list of turns
state.setdefault("notes", {})          # data source -> data notes text
state.setdefault("sid", uuid.uuid4().hex[:8])  # keeps two browser tabs' uploads apart


# --- data sources ------------------------------------------------------------------

def upload_db(files) -> tuple[Path, dict, str]:
    """Build (once per distinct set of files) a SQLite file from the uploads."""
    digest = hashlib.sha256(b"".join(f.name.encode() + f.getvalue() for f in files)).hexdigest()[:16]
    # The content hash is part of the path, so a new upload never reuses a cached schema.
    path = UPLOAD_DIR / f"{state.sid}_{digest}.sqlite"
    if state.get("upload_digest") != digest:
        UPLOAD_DIR.mkdir(exist_ok=True)
        if old := state.get("upload_path"):
            Path(old).unlink(missing_ok=True)
        state.upload_mapping = build_db([(f.name, f.getvalue()) for f in files], path)
        state.upload_digest, state.upload_path = digest, str(path)
    return path, state.upload_mapping, f"upload:{digest}"


def sidebar() -> tuple[Path | None, dict, str, str]:
    """Returns (db path, column-name mapping, conversation key, data notes)."""
    with st.sidebar:
        st.header("Data")
        ready = [name for name, p in SOURCES.items() if p.exists()]
        choice = st.radio("Database", ready + [UPLOAD])
        for name in SOURCES.keys() - set(ready):
            st.caption(f"{name.split(' (')[0]} not loaded yet: `{SETUP_HINT[name]}`")

        db, mapping, key = None, {}, choice
        if choice == UPLOAD:
            files = st.file_uploader("CSV or Excel files (one table per file or sheet)",
                                     type=["csv", "xlsx", "xls"], accept_multiple_files=True)
            if files:
                try:
                    db, mapping, key = upload_db(files)
                except Exception as e:  # bad encoding, broken Excel file, unsupported type...
                    st.error(f"Couldn't read the files: {e}")
        else:
            db = SOURCES[choice]

        # Widget state is dropped when a widget isn't shown, so notes live in our own dict
        # and are copied into the widget whenever this source's box appears again.
        notes_key = f"notes::{key}"
        if notes_key not in state:
            state[notes_key] = state.notes.get(key, DEFAULT_NOTES.get(key, ""))
        st.text_area("Data notes", key=notes_key, height=120,
                     placeholder="e.g. revenue = price + freight_value; 'late' means delivered "
                                 "after order_estimated_delivery_date",
                     help="Added to every question, like BIRD's evidence hints, which were "
                          "worth ~16 points of accuracy in our benchmark.")
        state.notes[key] = state[notes_key]
        if key in DEFAULT_NOTES:
            st.caption("Pre-filled with built-in notes for this database; edit freely.")

        if st.button("Clear conversation"):
            state.chats[key] = []
        model = config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.GROQ_MODEL
        st.caption(f"Model: {config.LLM_PROVIDER} / `{model}`")
    return db, mapping, key, state.notes[key]


# --- answering ------------------------------------------------------------------------

def answer(question: str, db: Path, notes: str, turns: list[dict]) -> dict:
    history = [(t["question"], t["sql"]) for t in turns if t.get("sql")][-MAX_HISTORY_TURNS:]
    try:
        r = run_agent(question, db, evidence=notes.strip() or None, history=history)
        text = explain(question, r.sql, r.result) if r.result is not None else None
    except requests.ConnectionError:
        return {"question": question, "error": f"Can't reach Ollama at {config.OLLAMA_HOST}. "
                                               "Is it running (`ollama serve`)?"}
    except requests.HTTPError as e:
        return {"question": question, "error": f"The LLM request failed: {e}"}
    return {"question": question, "agent": r, "sql": r.sql, "explanation": text}


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def show_turn(i: int, t: dict, mapping: dict) -> None:
    with st.chat_message("user"):
        st.markdown(t["question"])
    with st.chat_message("assistant"):
        if "error" in t:
            st.error(t["error"])
            return
        r = t["agent"]
        if r.result is None:
            st.warning("I couldn't write a query that runs on this data. The attempts are below.")
        else:
            if t["explanation"]:
                st.markdown(t["explanation"])
            # Show the user's own column names where we renamed them on upload.
            df = pd.DataFrame(r.result.rows, columns=original_names(r.result.columns, mapping))
            chart = pick_chart(df)
            if chart and chart.kind == "metric":
                st.metric(df.columns[0], _fmt(df.iat[0, 0]))
            elif chart:
                st.plotly_chart(chart.figure, width="stretch", key=f"chart-{i}")
                if chart.note:
                    st.caption(chart.note)
            st.dataframe(df, width="stretch", hide_index=True)
            rows = f"{len(df):,}{'+ (limit reached)' if r.result.truncated else ''} rows"
            st.caption(rows if r.ok else f"{rows} · the agent flagged this result as suspicious "
                                         f"but couldn't improve it: {r.attempts[-1].error}")
        with st.expander("SQL"):
            st.code(r.sql or r.attempts[-1].sql, language="sql")
        total = (r.select_ms + sum(a.latency_ms for a in r.attempts)) / 1000
        with st.expander(f"Attempts: {len(r.attempts)} · {total:.1f}s"):
            if r.selected_tables:
                st.caption(f"Schema too big for the context window; used tables: {', '.join(r.selected_tables)}")
            if r.history_used:
                st.caption(f"Used {r.history_used} earlier question(s) as context.")
            st.dataframe(pd.DataFrame([
                {"#": n, "status": a.error or "OK", "ms": round(a.latency_ms),
                 "tok/s": round(a.tokens_per_sec, 1) if a.tokens_per_sec else None,
                 "SQL": " ".join(a.sql.split())}
                for n, a in enumerate(r.attempts, 1)]), width="stretch", hide_index=True)


# --- page -----------------------------------------------------------------------------

db, mapping, key, notes = sidebar()
st.title("🔎 SQL Analyst Agent")
st.caption("Ask a question in plain English. The agent writes SQLite, runs it read-only, "
           "fixes its own mistakes and explains the result.")
if db is None:
    st.info("Upload one or more CSV / Excel files in the sidebar to start.")
    st.stop()

with st.expander("Schema"):
    st.code(get_schema(db, "names"), language=None)
    if mapping:
        st.caption("Renamed for SQL (your names are shown in results):")
        st.dataframe(pd.DataFrame([{"table": t, "from": info["source"], "column": c, "your name": o}
                                   for t, info in mapping.items() for c, o in info["columns"].items()]),
                     hide_index=True)

turns = state.chats.setdefault(key, [])
for i, t in enumerate(turns):
    show_turn(i, t, mapping)

if question := st.chat_input("Ask about this data, or follow up on the last answer"):
    with st.chat_message("user"):
        st.markdown(question)
    with st.spinner("Writing and running SQL..."):
        turns.append(answer(question, db, notes, turns))
    st.rerun()
