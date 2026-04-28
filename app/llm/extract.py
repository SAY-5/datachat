"""Extract the executable code block from a streamed LLM response."""

from __future__ import annotations

import re

# Match a python fenced block, with or without a language tag.
_BLOCK_RE = re.compile(
    r"```(?:python|py)?\s*\n(?P<body>.*?)```",
    flags=re.DOTALL | re.IGNORECASE,
)


def extract_code(text: str) -> str | None:
    """Return the first python fenced block in ``text`` or None.

    We deliberately accept *only* fenced blocks. Free-floating code
    that happens to look like Python is not extracted, because the
    sandbox runs it and we want unambiguous boundaries.
    """
    m = _BLOCK_RE.search(text)
    if not m:
        return None
    return m.group("body").rstrip() + "\n"
