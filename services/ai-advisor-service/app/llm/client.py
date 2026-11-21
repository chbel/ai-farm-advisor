"""Chat completion over OpenAI-compatible APIs serving open-weights models.

Providers are tried in order (Groq, then Cerebras). A provider is skipped for a
while after a rate-limit or server error, so a busy provider does not slow every
request down. When no provider is configured or all fail, callers get
`LLMUnavailable` and must degrade gracefully.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger("advisor.llm")

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
COOLDOWN_SECONDS = 60.0


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMReply:
    content: str
    provider: str
    model: str


class ChatModel(Protocol):
    async def chat(self, messages: list[dict], *, json_mode: bool = False,
                   max_tokens: int | None = None) -> LLMReply: ...


def strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models such as Qwen3."""
    text = _THINK.sub("", text)
    if "</think>" in text:  # opening tag cut off
        text = text.split("</think>", 1)[1]
    return text.strip()


def parse_json(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences and prose."""
    cleaned = strip_reasoning(text).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[cleaned.find("{"):] if "{" in cleaned else cleaned
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(cleaned)
        if not match:
            raise ValueError("No JSON object in model reply") from None
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Model reply is not a JSON object")
    return value


@dataclass
class Provider:
    name: str
    model: str
    client: AsyncOpenAI
    unavailable_until: float = 0.0


class FallbackChatModel:
    def __init__(self, providers: list[Provider]) -> None:
        self.providers = providers

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    async def chat(self, messages: list[dict], *, json_mode: bool = False,
                   max_tokens: int | None = None) -> LLMReply:
        errors = []
        now = time.monotonic()
        for provider in self.providers:
            if provider.unavailable_until > now:
                errors.append(f"{provider.name}: cooling down")
                continue
            kwargs = dict(model=provider.model, messages=messages,
                          temperature=settings.LLM_TEMPERATURE,
                          max_tokens=max_tokens or settings.LLM_MAX_OUTPUT_TOKENS)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                response = await provider.client.chat.completions.create(**kwargs)
            except (APITimeoutError, APIConnectionError) as exc:
                errors.append(f"{provider.name}: {type(exc).__name__}")
                provider.unavailable_until = now + COOLDOWN_SECONDS
                continue
            except APIStatusError as exc:
                # 429 (rate limit) and 5xx: try the next provider and back off.
                errors.append(f"{provider.name}: HTTP {exc.status_code}")
                if exc.status_code == 429 or exc.status_code >= 500:
                    provider.unavailable_until = now + COOLDOWN_SECONDS
                continue
            content = response.choices[0].message.content or ""
            return LLMReply(content=strip_reasoning(content), provider=provider.name, model=provider.model)

        logger.warning("All LLM providers failed: %s", "; ".join(errors) or "none configured")
        raise LLMUnavailable("; ".join(errors) or "No LLM provider configured")


def build_chat_model() -> FallbackChatModel:
    providers = []
    for name, key, base_url, model in (
        ("groq", settings.GROQ_API_KEY, settings.GROQ_BASE_URL, settings.GROQ_MODEL),
        ("cerebras", settings.CEREBRAS_API_KEY, settings.CEREBRAS_BASE_URL, settings.CEREBRAS_MODEL),
    ):
        if key is None:
            continue
        client = AsyncOpenAI(api_key=key.get_secret_value(), base_url=base_url,
                             timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=1)
        providers.append(Provider(name=name, model=model, client=client))
    if not providers:
        logger.warning("No LLM API key configured: answers will contain sources and data only.")
    return FallbackChatModel(providers)
