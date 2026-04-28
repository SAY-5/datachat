"""End-to-end FastAPI HTTP + SSE tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.app import build_app
from app.llm.mock import MockLLMProvider
from app.sandbox import SandboxConfig, SandboxRunner
from app.store import Store


@pytest.fixture
def client(tmp_path: Path):
    s = Store(f"sqlite:///{tmp_path}/db.sqlite")
    runner = SandboxRunner(SandboxConfig(wall_seconds=20.0))
    app = build_app(
        store=s,
        llm=MockLLMProvider(chunk_delay_ms=0),
        runner=runner,
        data_dir=str(tmp_path / "data"),
    )
    with TestClient(app) as c:
        yield c


def _parse_sse(body: bytes) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in body.decode("utf-8").split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if event and data is not None:
            try:
                out.append((event, json.loads(data)))
            except json.JSONDecodeError:
                out.append((event, {"_raw": data}))
    return out


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_create_session_and_list(client):
    r = client.post("/v1/sessions", json={"title": "hi"})
    assert r.status_code == 200
    sid = r.json()["id"]
    r2 = client.get("/v1/sessions")
    assert any(s["id"] == sid for s in r2.json()["items"])


def test_get_session_404(client):
    r = client.get("/v1/sessions/nope")
    assert r.status_code == 404


def test_full_streaming_loop_produces_chart(client):
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    r = client.post(
        f"/v1/sessions/{sess['id']}/messages",
        json={"content": "show me a histogram of revenue"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.content)
    kinds = [e[0] for e in events]
    assert "user_message" in kinds
    assert "token" in kinds
    assert "code" in kinds
    assert "exec_result" in kinds
    assert "done" in kinds
    exec_event = next(e for e in events if e[0] == "exec_result")[1]
    assert exec_event["ok"] is True, (
        f"exec failed: error_class={exec_event.get('error_class')!r} "
        f"error_message={exec_event.get('error_message')!r} "
        f"stderr={exec_event.get('stderr', '')!r}"
    )
    assert exec_event["figure"] is not None
    # Persistence: the assistant message lands in the session.
    detail = client.get(f"/v1/sessions/{sess['id']}").json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][-1]["figure_json"]


def test_stats_records_latency(client):
    sess = client.post("/v1/sessions", json={"dataset": "demo_orders"}).json()
    client.post(
        f"/v1/sessions/{sess['id']}/messages",
        json={"content": "summary"},
    )
    s = client.get("/v1/stats").json()
    assert s["count"] >= 1
    assert s["p50_ms"] > 0


def test_oversized_message_rejected(client):
    sess = client.post("/v1/sessions", json={}).json()
    r = client.post(
        f"/v1/sessions/{sess['id']}/messages",
        json={"content": "x" * 20_000},
    )
    assert r.status_code == 422


def test_datasets_endpoint(client):
    r = client.get("/v1/datasets")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(d["id"] == "demo_orders" for d in items)
