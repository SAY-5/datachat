"""OpenAI Chat Completions provider.

Optional dep — install via ``pip install datachat[openai]``. Real
deployments use this; tests use the mock so they don't need an API
key.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .base import LLMProvider, Message, StreamToken


@dataclass
class OpenAIProvider(LLMProvider):
    name: str = "openai"
    api_key: str | None = None

    def __post_init__(self) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "OpenAI provider requires `pip install datachat[openai]`"
            ) from e
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self._client = AsyncOpenAI(api_key=key)

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamToken]:
        oai_msgs = [{"role": m.role, "content": m.content} for m in messages]
        kwargs: dict = {
            "model": model,
            "messages": oai_msgs,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        async with self._client.chat.completions.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content.delta":
                    yield StreamToken(content=event.delta or "")
                elif event.type == "content.done":
                    yield StreamToken(content="", finish_reason="stop")
