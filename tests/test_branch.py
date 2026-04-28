"""Conversation branching: fork an existing session at a chosen
message, copy that prefix into a new session, leave the original
intact."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import build_app
from app.llm.mock import MockLLMProvider
from app.sandbox import SandboxRunner
from app.store import Store


@pytest.fixture()
def client(tmp_path):
    dsn = f"sqlite:///{tmp_path / 'datachat.db'}"
    app = build_app(
        store=Store(dsn=dsn),
        llm=MockLLMProvider(chunk_delay_ms=0),
        runner=SandboxRunner(),
        data_dir=str(tmp_path),
    )
    with TestClient(app) as c:
        yield c


def _send(c, sid, content):
    return c.post(f"/v1/sessions/{sid}/messages", json={"content": content})


def test_fork_copies_prefix_and_keeps_original(client):
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    sid = sess["id"]
    # Send 3 messages, capture each user message ID off the persisted
    # session.
    for q in ["how many rows?", "top 5 by revenue", "summary"]:
        _send(client, sid, q)
    full = client.get(f"/v1/sessions/{sid}").json()
    msgs = full["messages"]
    # Pick the user message of the second turn as the fork anchor.
    user_turns = [m for m in msgs if m["role"] == "user"]
    assert len(user_turns) >= 2
    anchor = user_turns[1]

    forked = client.post(
        f"/v1/sessions/{sid}/fork",
        json={"anchor_message_id": anchor["id"], "title": "branch A"},
    )
    assert forked.status_code == 200, forked.text
    fork = forked.json()
    assert fork["forked_from_session_id"] == sid
    assert fork["forked_at_message_id"] == anchor["id"]
    assert fork["dataset"] == sess["dataset"]
    assert fork["title"] == "branch A"

    # The fork copies messages with NEW ids (so it doesn't collide
    # with the source) — match by content + role + position.
    src_prefix = [m for m in msgs if m["created_at"] <= anchor["created_at"]]
    assert len(fork["messages"]) == len(src_prefix), (
        f"fork len={len(fork['messages'])} expected={len(src_prefix)}"
    )
    for src_m, fork_m in zip(src_prefix, fork["messages"], strict=True):
        assert src_m["role"] == fork_m["role"]
        assert src_m["content"] == fork_m["content"]
        # Fork messages must have *new* IDs.
        assert src_m["id"] != fork_m["id"]
    # The metadata pointer goes back to the original anchor.
    assert fork["forked_at_message_id"] == anchor["id"]

    # Source session is unchanged.
    after = client.get(f"/v1/sessions/{sid}").json()
    assert len(after["messages"]) == len(full["messages"])


def test_fork_anchor_must_belong_to_source(client):
    a = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    b = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    _send(client, a["id"], "row count?")
    _send(client, b["id"], "different question")
    a_full = client.get(f"/v1/sessions/{a['id']}").json()
    a_msg = a_full["messages"][0]["id"]
    # Trying to fork session b with an anchor from session a → 404.
    r = client.post(
        f"/v1/sessions/{b['id']}/fork",
        json={"anchor_message_id": a_msg},
    )
    assert r.status_code == 404


def test_fork_writes_to_fork_do_not_touch_source(client):
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    sid = sess["id"]
    _send(client, sid, "what is the dataset?")
    full = client.get(f"/v1/sessions/{sid}").json()
    anchor = full["messages"][0]
    forked = client.post(
        f"/v1/sessions/{sid}/fork",
        json={"anchor_message_id": anchor["id"]},
    ).json()
    # New question only on the fork.
    _send(client, forked["id"], "show me a histogram of revenue")
    src_after = client.get(f"/v1/sessions/{sid}").json()
    fork_after = client.get(f"/v1/sessions/{forked['id']}").json()
    assert len(fork_after["messages"]) > len(src_after["messages"])
