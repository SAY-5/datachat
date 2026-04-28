"""LLM provider protocol shared by mock + OpenAI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str


@dataclass(slots=True)
class StreamToken:
    """One delta from the streaming endpoint. ``content`` may be the
    empty string for synthetic events (heartbeat, finish)."""

    content: str
    finish_reason: str | None = None


class LLMProvider(Protocol):
    name: str

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamToken]:
        """Yield tokens as they arrive."""
        ...
