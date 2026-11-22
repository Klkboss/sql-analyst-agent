"""Loop tests with a scripted fake LLM: each call returns the next canned reply."""
import pytest

from agent import llm, loop
from agent.generator import extract_sql
from agent.llm import LLMResponse


@pytest.fixture
def fake_llm(monkeypatch):
    def install(*replies):
        it = iter(replies)
        monkeypatch.setattr(llm, "chat", lambda messages: LLMResponse(next(it), 1.0, None))
    return install


def test_extract_sql_from_code_block():
    assert extract_sql("Here you go:\n```sql\nSELECT 1;\n```\nDone.") == "SELECT 1;"
    assert extract_sql("SELECT 2") == "SELECT 2"


def test_retries_after_error_then_succeeds(db, fake_llm):
    fake_llm("SELECT nme FROM artist", "SELECT name FROM artist")
    r = loop.run_agent("names?", db)
    assert r.ok and len(r.attempts) == 2
    assert "no such column" in r.attempts[0].error
    assert len(r.result.rows) == 4


def test_gives_up_after_max_retries(db, fake_llm):
    fake_llm(*["DROP TABLE artist"] * 10)
    r = loop.run_agent("drop it", db, max_retries=3)
    assert not r.ok and len(r.attempts) == 4  # 1 try + 3 retries
    assert r.result is None


def test_empty_result_is_accepted_when_model_repeats_it(db, fake_llm):
    empty = "SELECT name FROM artist WHERE name = 'Nobody'"
    fake_llm(empty, empty)
    r = loop.run_agent("who is Nobody?", db)
    assert r.ok and len(r.attempts) == 2
    assert "0 rows" in r.attempts[0].error and r.result.rows == []
