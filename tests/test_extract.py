from __future__ import annotations

from app.llm.extract import extract_code


def test_extract_python_block():
    text = "Here you go.\n\n```python\nresult = 42\n```\n"
    assert extract_code(text) == "result = 42\n"


def test_extract_unmarked_block():
    text = "```\nresult = 1\n```"
    assert extract_code(text) == "result = 1\n"


def test_no_block_returns_none():
    assert extract_code("just prose") is None


def test_first_block_only():
    text = "```python\na = 1\n```\nthen\n```python\nb = 2\n```"
    assert extract_code(text).strip() == "a = 1"


def test_strips_trailing_whitespace_keeps_newline():
    text = "```python\nresult = 1   \n  \n```"
    out = extract_code(text)
    assert out.endswith("\n")
    assert "result = 1" in out
