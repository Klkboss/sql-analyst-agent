import pytest

from agent import config, llm


class FakeResp:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.headers = {"retry-after": str(retry_after)} if retry_after is not None else {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "```sql\nSELECT 1\n```"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "completion_time": 0.1,
                          "prompt_tokens_details": {"cached_tokens": 40}}}


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "test")
    monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "GROQ_REASONING_EFFORT", "low")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    sent = []

    def use(*responses):
        it = iter(responses)
        monkeypatch.setattr(llm.requests, "post", lambda url, **kw: sent.append(kw["json"]) or next(it))
        return sent
    return use


def test_short_rate_limit_waits_and_retries(groq):
    sent = groq(FakeResp(429, retry_after=2), FakeResp(200))
    before = dict(llm.usage)
    text, _ = llm._call_groq([{"role": "user", "content": "q"}])
    assert "SELECT 1" in text and len(sent) == 2
    # reasoning stays out of the reply, and its length is capped
    assert sent[0]["include_reasoning"] is False and sent[0]["reasoning_effort"] == "low"
    assert llm.usage["cached"] - before["cached"] == 40


def test_daily_cap_raises_instead_of_blocking(groq):
    groq(FakeResp(429, retry_after=3600))
    with pytest.raises(llm.RateLimited):
        llm._call_groq([{"role": "user", "content": "q"}])
