"""Helpers for emitting Server-Sent Events."""

from __future__ import annotations

import json
from typing import Any


def sse(event: str, data: Any) -> bytes:
    """Format one SSE frame. ``data`` is JSON-encoded.

    Must use ``\\n\\n`` as the terminator. Browsers reassemble the
    event on the empty line.
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()
