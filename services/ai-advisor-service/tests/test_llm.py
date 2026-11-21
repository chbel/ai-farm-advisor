"""LLM client: reply parsing and provider fallback."""

import httpx
import openai
import pytest

from app.llm.client import FallbackChatModel, LLMUnavailable, Provider, parse_json, strip_reasoning


def test_strip_reasoning_blocks():
    assert strip_reasoning("<think>internal</think>\nAnswer") == "Answer"
    assert strip_reasoning("cut off reasoning</think>Answer") == "Answer"
    assert strip_reasoning("Plain") == "Plain"


@pytest.mark.parametrize(
    "reply",
    ['{"answer": "x"}', '<think>hmm</think>{"answer": "x"}', '```json\n{"answer": "x"}\n```',
     'Here you go: {"answer": "x"} thanks'],
)
def test_parse_json_tolerates_wrappers(reply):
    assert parse_json(reply) == {"answer": "x"}


@pytest.mark.parametrize("reply", ["no json here", "[1, 2]"])
def test_parse_json_rejects_non_objects(reply):
    with pytest.raises(ValueError):
        parse_json(reply)


class _Completions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        message = type("M", (), {"content": self.outcome})
        return type("R", (), {"choices": [type("C", (), {"message": message})]})


def provider(name: str, outcome) -> Provider:
    completions = _Completions(outcome)
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})})
    return Provider(name=name, model=f"{name}-model", client=client)


def rate_limited() -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.test/chat"))
    return openai.RateLimitError("rate limited", response=response, body=None)


async def test_falls_back_to_next_provider_on_rate_limit_and_cools_down():
    groq, cerebras = provider("groq", rate_limited()), provider("cerebras", "<think>x</think>Bonjour")
    model = FallbackChatModel([groq, cerebras])

    reply = await model.chat([{"role": "user", "content": "hi"}])
    assert (reply.provider, reply.content) == ("cerebras", "Bonjour")

    await model.chat([{"role": "user", "content": "again"}])
    assert groq.client.chat.completions.calls == 1  # skipped while cooling down


async def test_all_providers_failing_raises_unavailable():
    model = FallbackChatModel([provider("groq", rate_limited())])
    with pytest.raises(LLMUnavailable, match="HTTP 429"):
        await model.chat([{"role": "user", "content": "hi"}])


async def test_no_provider_configured():
    model = FallbackChatModel([])
    assert not model.configured
    with pytest.raises(LLMUnavailable):
        await model.chat([{"role": "user", "content": "hi"}])
