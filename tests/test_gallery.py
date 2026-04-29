"""v3: chart gallery — pin assistant figures, list across sessions,
unpin. Pinning the same message twice updates title/note."""

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


def _send_chart_question(c, sid):
    """The mock LLM emits plotly code for histogram-shaped queries.
    Returns the assistant message that has a figure attached."""
    c.post(f"/v1/sessions/{sid}/messages",
           json={"content": "show me a histogram of revenue"})
    detail = c.get(f"/v1/sessions/{sid}").json()
    asst = next(m for m in reversed(detail["messages"]) if m["role"] == "assistant")
    assert asst["figure_json"], (
        f"expected an assistant figure; got status={asst.get('status')!r}"
    )
    return asst


def test_pin_unpin_round_trip(client):
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    sid = sess["id"]
    asst = _send_chart_question(client, sid)
    r = client.post(f"/v1/sessions/{sid}/pins",
                    json={"message_id": asst["id"], "title": "Revenue distribution"})
    assert r.status_code == 200
    pin_id = r.json()["id"]

    items = client.get("/v1/pins").json()["items"]
    assert any(p["id"] == pin_id for p in items)
    me = next(p for p in items if p["id"] == pin_id)
    assert me["title"] == "Revenue distribution"
    assert me["dataset"] == "demo_orders"
    assert me["figure_json"]
    # Gallery includes the user question that produced the figure.
    assert me["user_question"] == "show me a histogram of revenue"

    assert client.delete(f"/v1/pins/{pin_id}").status_code == 200
    items = client.get("/v1/pins").json()["items"]
    assert all(p["id"] != pin_id for p in items)


def test_pin_idempotent_updates_title(client):
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    sid = sess["id"]
    asst = _send_chart_question(client, sid)

    r1 = client.post(f"/v1/sessions/{sid}/pins",
                     json={"message_id": asst["id"], "title": "first"})
    r2 = client.post(f"/v1/sessions/{sid}/pins",
                     json={"message_id": asst["id"], "title": "second", "note": "kept"})
    assert r1.json()["id"] == r2.json()["id"]
    items = {p["id"]: p for p in client.get("/v1/pins").json()["items"]}
    pin = items[r1.json()["id"]]
    assert pin["title"] == "second"
    assert pin["note"] == "kept"


def test_pin_rejects_message_without_figure(client):
    """A user message has no figure_json — pinning it must 404."""
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    sid = sess["id"]
    client.post(f"/v1/sessions/{sid}/messages",
                json={"content": "show me a histogram of revenue"})
    detail = client.get(f"/v1/sessions/{sid}").json()
    user_msg = next(m for m in detail["messages"] if m["role"] == "user")
    r = client.post(f"/v1/sessions/{sid}/pins",
                    json={"message_id": user_msg["id"]})
    assert r.status_code == 404


def test_gallery_filters_by_session(client):
    """`/v1/pins?session_id=` only returns pins from that session."""
    s1 = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    s2 = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    a1 = _send_chart_question(client, s1["id"])
    a2 = _send_chart_question(client, s2["id"])
    client.post(f"/v1/sessions/{s1['id']}/pins", json={"message_id": a1["id"]})
    client.post(f"/v1/sessions/{s2['id']}/pins", json={"message_id": a2["id"]})

    items = client.get(f"/v1/pins?session_id={s1['id']}").json()["items"]
    assert all(p["session_id"] == s1["id"] for p in items)
    assert len(items) == 1


def test_gallery_pin_survives_session_branch(client):
    """A pin on the source survives forking the session — both source
    and branch can have their own pins, gallery shows both."""
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    sid = sess["id"]
    asst = _send_chart_question(client, sid)
    pin_src = client.post(f"/v1/sessions/{sid}/pins",
                          json={"message_id": asst["id"], "title": "source"}).json()

    fork = client.post(f"/v1/sessions/{sid}/fork",
                       json={"anchor_message_id": asst["id"]}).json()
    asst_fork = next(m for m in reversed(fork["messages"]) if m["role"] == "assistant")
    pin_fork = client.post(f"/v1/sessions/{fork['id']}/pins",
                           json={"message_id": asst_fork["id"], "title": "branch"}).json()

    items = client.get("/v1/pins").json()["items"]
    by_id = {p["id"]: p for p in items}
    assert pin_src["id"] in by_id and pin_fork["id"] in by_id
    assert by_id[pin_src["id"]]["title"] == "source"
    assert by_id[pin_fork["id"]]["title"] == "branch"
