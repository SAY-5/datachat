from __future__ import annotations

from pathlib import Path

from app.store import Store


def test_session_roundtrip(tmp_path: Path):
    s = Store(f"sqlite:///{tmp_path}/t.db")
    sess = s.create_session(dataset="demo_orders", title="hello")
    assert sess.id

    fetched = s.get_session(sess.id)
    assert fetched is not None
    assert fetched.dataset == "demo_orders"
    assert fetched.title == "hello"
    assert fetched.messages == []


def test_message_added_in_order(tmp_path: Path):
    s = Store(f"sqlite:///{tmp_path}/t.db")
    sess = s.create_session(dataset=None, title=None)
    s.add_message(sess.id, "user", "hi")
    s.add_message(sess.id, "assistant", "hello back",
                  code="result = 1\n", elapsed_ms=42)
    fresh = s.get_session(sess.id)
    assert [m.role for m in fresh.messages] == ["user", "assistant"]
    assert fresh.messages[-1].elapsed_ms == 42


def test_list_sessions_recent_first(tmp_path: Path):
    s = Store(f"sqlite:///{tmp_path}/t.db")
    a = s.create_session(dataset=None, title="a")
    b = s.create_session(dataset=None, title="b")
    items = s.list_sessions()
    assert {x.id for x in items[:2]} == {a.id, b.id}
