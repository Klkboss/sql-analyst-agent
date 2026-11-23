"""Loop tests with a scripted fake LLM: each call returns the next canned reply."""
import pytest

from agent import config, llm, loop
from agent.generator import STRICT_NOTE, extract_sql, parse_tables
from agent.llm import LLMResponse


@pytest.fixture
def fake_llm(monkeypatch):
    """install(*replies) -> list that collects the messages of every LLM call."""
    def install(*replies):
        it, calls = iter(replies), []

        def chat(messages):
            calls.append([dict(m) for m in messages])
            return LLMResponse(next(it), 1.0, None)
        monkeypatch.setattr(llm, "chat", chat)
        return calls
    return install


def test_extract_sql_from_code_block():
    assert extract_sql("Here you go:\n```sql\nSELECT 1;\n```\nDone.") == "SELECT 1;"
    assert extract_sql("SELECT 2") == "SELECT 2"


def test_parse_tables_keeps_only_real_names():
    assert parse_tables("Album, `artist`, made_up", ["album", "artist", "track"]) == ["album", "artist"]


def test_retries_after_error_then_succeeds(db, fake_llm):
    fake_llm("SELECT nme FROM artist", "SELECT name FROM artist")
    r = loop.run_agent("names?", db)
    assert r.ok and len(r.attempts) == 2
    assert "no such column" in r.attempts[0].error
    assert len(r.result.rows) == 4 and r.sql == "SELECT name FROM artist"


def test_gives_up_after_max_retries(db, fake_llm):
    fake_llm(*["DROP TABLE artist"] * 10)
    r = loop.run_agent("drop it", db, max_retries=3)
    assert not r.ok and len(r.attempts) == 4  # 1 try + 3 retries
    assert r.result is None and r.sql is None


def test_empty_result_is_accepted_when_model_repeats_it(db, fake_llm):
    empty = "SELECT name FROM artist WHERE name = 'Nobody'"
    fake_llm(empty, empty)
    r = loop.run_agent("who is Nobody?", db)
    assert r.ok and len(r.attempts) == 2
    assert "0 rows" in r.attempts[0].error and r.result.rows == []


# --- Fix 1: zero aggregate ------------------------------------------------------

COUNT_NOBODY = "SELECT COUNT(*) FROM artist WHERE name = 'Nobody'"


def test_zero_count_passes_without_fix(db, fake_llm):
    fake_llm(COUNT_NOBODY)
    r = loop.run_agent("how many Nobody?", db, zero_agg_suspicious=False)
    assert r.ok and len(r.attempts) == 1


def test_zero_count_is_suspicious_with_fix(db, fake_llm):
    fake_llm(COUNT_NOBODY, "SELECT COUNT(*) FROM artist WHERE name = 'AC/DC'")
    r = loop.run_agent("how many Nobody?", db, zero_agg_suspicious=True)
    assert "single row of 0/NULL" in r.attempts[0].error
    assert r.ok and r.result.rows == [(1,)]


# --- Fix 2: strict retry prompt --------------------------------------------------

@pytest.mark.parametrize("strict", [False, True])
def test_strict_note_only_when_switched_on(db, fake_llm, strict):
    calls = fake_llm("SELECT name FROM artist WHERE name = 'Nobody'", "SELECT name FROM artist")
    loop.run_agent("q", db, strict_retry=strict)
    assert (STRICT_NOTE in calls[1][-1]["content"]) is strict


# --- Context budget / table selection -------------------------------------------

def test_small_schema_skips_table_selection(db, fake_llm):
    calls = fake_llm("SELECT 1")
    r = loop.run_agent("q", db)
    assert r.selected_tables is None and len(calls) == 1


def _size(db, question, tables=None, history=()):
    """Estimated prompt tokens, so tests pick budgets from real sizes, not magic numbers."""
    from agent.generator import estimate_tokens, initial_messages
    from agent.schema import get_schema
    return estimate_tokens(initial_messages(get_schema(db, "full", tables), question, None, history))


def test_big_schema_triggers_table_selection(db, fake_llm, monkeypatch):
    small, full = _size(db, "artist names?", ("artist",)), _size(db, "artist names?")
    assert small < full
    monkeypatch.setattr(config, "CONTEXT_LIMIT", loop.RESERVED_TOKENS + (small + full) // 2)
    calls = fake_llm("artist", "SELECT name FROM artist")
    r = loop.run_agent("artist names?", db)
    assert r.selected_tables == ["artist"] and not r.ctx_overflow
    system = calls[1][0]["content"]  # the SQL call only saw the selected table
    assert "Table artist:" in system and "Table album:" not in system
    assert r.ok


def test_still_too_big_is_flagged_not_hidden(db, fake_llm, monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_LIMIT", loop.RESERVED_TOKENS + 10)
    fake_llm("artist", "SELECT name FROM artist")
    assert loop.run_agent("q", db).ctx_overflow


# --- Follow-up questions (conversation history) -----------------------------------

HISTORY = [(f"question {i}", f"SELECT {i}") for i in range(1, 6)]


def test_history_goes_in_as_earlier_turns_last_three_only(db, fake_llm):
    calls = fake_llm("SELECT name FROM artist")
    r = loop.run_agent("now only the first one", db, history=HISTORY)
    roles = [m["role"] for m in calls[0]]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert calls[0][1]["content"] == "question 3"            # 1 and 2 were cut (MAX_HISTORY_TURNS = 3)
    assert calls[0][2]["content"] == "```sql\nSELECT 3\n```"  # the model sees its own earlier SQL
    assert calls[0][-1]["content"] == "now only the first one"
    assert r.history_used == 3


def test_tight_budget_keeps_last_turn_and_tells_table_selection(db, fake_llm, monkeypatch):
    # Room for the selected schema + one turn, not for the full schema even with one turn.
    q, last = "and sorted by name?", HISTORY[-1:]
    fits, too_big = _size(db, q, ("artist",), last), _size(db, q, None, last)
    assert fits < too_big
    monkeypatch.setattr(config, "CONTEXT_LIMIT", loop.RESERVED_TOKENS + fits)
    calls = fake_llm("artist", "SELECT name FROM artist")
    r = loop.run_agent("and sorted by name?", db, history=HISTORY)
    assert r.selected_tables == ["artist"] and r.history_used == 1
    assert "question 5" in calls[0][0]["content"]       # selection saw the previous question
    assert calls[1][1]["content"] == "question 5"       # and the SQL call kept that turn


# --- SQL-shape self-check -----------------------------------------------------------

HIDDEN = "SELECT a.name FROM artist a JOIN album b ON b.artist_id = a.id GROUP BY a.name ORDER BY COUNT(*) DESC"
SHOWN = "SELECT a.name, COUNT(*) AS albums FROM artist a JOIN album b ON b.artist_id = a.id GROUP BY a.name ORDER BY albums DESC"


def test_shape_check_retries_hidden_ranking_metric(db, fake_llm):
    calls = fake_llm(HIDDEN, SHOWN)
    r = loop.run_agent("artists with most albums", db, check_shape=True)
    assert "ranks rows by COUNT(*)" in r.attempts[0].error
    assert r.ok and r.result.columns == ["name", "albums"]
    assert len(calls) == 2


def test_shape_check_off_accepts_it(db, fake_llm):
    fake_llm(HIDDEN)
    assert loop.run_agent("artists with most albums", db, check_shape=False).ok


def test_shape_check_accepts_confirmed_resubmission(db, fake_llm):
    fake_llm(HIDDEN, HIDDEN)  # "the question asked for names only" -> same SQL = confirmed
    r = loop.run_agent("names of artists ordered by album count", db, check_shape=True)
    assert r.ok and len(r.attempts) == 2


def test_keyword_table_alias_is_repaired_before_running(db, fake_llm):
    fake_llm("SELECT or.name FROM artist or JOIN album al ON al.artist_id = or.id")
    r = loop.run_agent("names", db)
    assert r.ok and len(r.attempts) == 1
    assert r.sql == "SELECT or_.name FROM artist or_ JOIN album al ON al.artist_id = or_.id"


def test_other_keyword_errors_explain_themselves(db, fake_llm):
    fake_llm("SELECT name AS order FROM artist", "SELECT name AS artist_name FROM artist")
    r = loop.run_agent("names", db)
    assert '"order" is a reserved SQL keyword' in r.attempts[0].error
    assert r.ok


def test_other_syntax_errors_get_no_keyword_hint(db, fake_llm):
    fake_llm("SELECT name FROM artist WHERE", "SELECT name FROM artist")
    assert "reserved" not in loop.run_agent("names", db).attempts[0].error


def test_repeated_failing_sql_is_called_out(db, fake_llm):
    calls = fake_llm("SELECT nme FROM artist", "SELECT nme FROM artist", "SELECT name FROM artist")
    r = loop.run_agent("names", db)
    assert "exact query before" not in r.attempts[0].error
    assert "exact query before" in r.attempts[1].error and r.ok
