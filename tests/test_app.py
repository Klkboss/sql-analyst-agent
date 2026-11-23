"""Headless smoke test of the Streamlit app (AppTest) with a scripted fake LLM."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import llm
from agent.llm import LLMResponse

APP = Path(__file__).parent.parent / "app.py"
pytestmark = pytest.mark.skipif(not (APP.parent / "data" / "chinook.sqlite").exists(),
                                reason="needs data/chinook.sqlite (python data/download_chinook.py)")


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def install(*replies):
        it = iter(replies)

        def chat(messages):
            calls.append(messages)
            return LLMResponse(next(it), 1.0, None)
        monkeypatch.setattr(llm, "chat", chat)
        return calls
    return install


def test_question_then_follow_up(fake_llm):
    calls = fake_llm(
        "```sql\nSELECT Name FROM Genre ORDER BY GenreId LIMIT 3\n```", "Rock, Jazz and Metal.",
        "```sql\nSELECT COUNT(*) FROM Genre\n```", "There are 25 genres.",
    )
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception

    at.chat_input[0].set_value("first 3 genres").run()
    assert not at.exception
    assert "Rock, Jazz and Metal." in [m.value for m in at.markdown]
    result = next(d.value for d in at.dataframe if "Name" in d.value.columns)  # not the attempt log
    assert result["Name"].tolist() == ["Rock", "Jazz", "Metal"]

    at.chat_input[0].set_value("how many in total?").run()
    assert not at.exception
    assert at.metric[0].value == "25"                   # 1x1 number -> metric card
    sql_call = calls[2]                                  # follow-up's SQL request
    assert sql_call[1]["content"] == "first 3 genres"   # earlier question went in as history
    assert "SELECT Name FROM Genre" in sql_call[2]["content"]


def test_example_button_asks_the_question(fake_llm):
    calls = fake_llm("```sql\nSELECT COUNT(*) FROM Album\n```", "AC/DC has 2 albums.")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.button(key="example-4").click().run()
    assert not at.exception
    assert calls[0][-1]["content"] == "How many albums does the artist AC/DC have?"
    assert not [b for b in at.button if b.key and b.key.startswith("example-")]  # hidden once chatting


def test_data_notes_reach_the_prompt(fake_llm):
    calls = fake_llm("```sql\nSELECT 1\n```", "One.")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.text_area[0].set_value("Milliseconds is track length.").run()
    at.chat_input[0].set_value("anything").run()
    assert not at.exception
    assert "Hint: Milliseconds is track length." in calls[0][-1]["content"]


def test_olist_has_built_in_join_notes(fake_llm):
    if not (APP.parent / "data" / "olist.sqlite").exists():
        pytest.skip("needs data/olist.sqlite")
    calls = fake_llm("```sql\nSELECT 1\n```", "One.")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.sidebar.radio[0].set_value("Olist (Brazilian e-commerce)").run()
    assert "order_reviews -> order_items (order_id) -> products" in at.text_area[0].value
    at.chat_input[0].set_value("anything").run()
    assert "Hint: Join paths:" in calls[0][-1]["content"]
