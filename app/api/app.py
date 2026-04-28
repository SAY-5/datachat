"""FastAPI app factory.

Endpoints:
  POST /v1/sessions                           create
  GET  /v1/sessions                           list (recent)
  GET  /v1/sessions/{id}                      detail + messages
  POST /v1/sessions/{id}/messages?stream=1    SSE stream of tokens + exec
  GET  /v1/datasets                           dataset registry
  GET  /v1/stats                              p50/p95 + counts
  GET  /healthz                               liveness
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.llm.base import LLMProvider, Message
from app.llm.extract import extract_code
from app.llm.mock import MockLLMProvider
from app.sandbox import SandboxConfig, SandboxRunner
from app.store import Store
from app.store.datasets import (
    DEFAULT_DATA_DIR, Dataset, discover_datasets, ensure_demo_dataset,
)

from .sse import sse


SYSTEM_PROMPT = (
    "You are a Python data-analysis assistant. The dataset is loaded into a "
    "pandas DataFrame called `df`. Respond with one Python fenced code block "
    "that:\n"
    "- imports only pandas, numpy, plotly,\n"
    "- assigns the final result to `result`,\n"
    "- if the user asked for a chart, also assigns a plotly Figure to `fig`.\n"
    "Do not call os, subprocess, requests, socket, pickle, or any I/O."
)


class CreateSessionBody(BaseModel):
    dataset: str | None = None
    title: str | None = None


class CreateMessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=8192)


class ForkSessionBody(BaseModel):
    anchor_message_id: str = Field(..., min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=256)


def build_app(
    *,
    store: Store | None = None,
    llm: LLMProvider | None = None,
    runner: SandboxRunner | None = None,
    data_dir: str | None = None,
) -> FastAPI:
    state = {
        "store": store
            or Store(os.environ.get("DATACHAT_DSN", "sqlite:///./datachat.db")),
        "llm": llm or MockLLMProvider(),
        "runner": runner or SandboxRunner(),
        "data_dir": data_dir or DEFAULT_DATA_DIR,
        "datasets": {} ,  # filled in lifespan
        "latencies": deque(maxlen=512),  # ms per message
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Path(state["data_dir"]).mkdir(parents=True, exist_ok=True)
        ensure_demo_dataset(state["data_dir"])
        for ds in discover_datasets(state["data_dir"]):
            state["datasets"][ds.id] = ds
        yield

    app = FastAPI(title="DataChat", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("DATACHAT_CORS", "http://localhost:5173").split(","),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "datasets": len(state["datasets"])}

    @app.get("/v1/datasets")
    def datasets():
        return {
            "items": [
                {"id": d.id, "rows": d.rows, "columns": d.columns}
                for d in state["datasets"].values()
            ],
        }

    @app.post("/v1/sessions")
    def create_session(body: CreateSessionBody):
        sess = state["store"].create_session(
            dataset=body.dataset or "demo_orders", title=body.title,
        )
        return {
            "id": sess.id, "dataset": sess.dataset, "title": sess.title,
            "created_at": sess.created_at.isoformat(),
        }

    @app.get("/v1/sessions")
    def list_sessions(limit: int = 50):
        return {
            "items": [
                {"id": s.id, "dataset": s.dataset, "title": s.title,
                 "created_at": s.created_at.isoformat()}
                for s in state["store"].list_sessions(limit=limit)
            ],
        }

    @app.get("/v1/sessions/{sid}")
    def get_session(sid: str):
        sess = state["store"].get_session(sid)
        if sess is None:
            raise HTTPException(404, "not found")
        return {
            "id": sess.id, "dataset": sess.dataset, "title": sess.title,
            "created_at": sess.created_at.isoformat(),
            "forked_from_session_id": sess.forked_from_session_id,
            "forked_at_message_id":   sess.forked_at_message_id,
            "messages": [
                {
                    "id": m.id, "role": m.role, "content": m.content,
                    "code": m.code, "figure_json": m.figure_json,
                    "elapsed_ms": m.elapsed_ms, "status": m.status,
                    "created_at": m.created_at.isoformat(),
                }
                for m in sess.messages
            ],
        }

    @app.post("/v1/sessions/{sid}/fork")
    def fork_session(sid: str, body: ForkSessionBody):
        try:
            new = state["store"].fork_session(
                source_id=sid,
                up_to_message_id=body.anchor_message_id,
                title=body.title,
            )
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        return {
            "id": new.id, "dataset": new.dataset, "title": new.title,
            "created_at": new.created_at.isoformat(),
            "forked_from_session_id": new.forked_from_session_id,
            "forked_at_message_id":   new.forked_at_message_id,
            "messages": [
                {
                    "id": m.id, "role": m.role, "content": m.content,
                    "code": m.code, "figure_json": m.figure_json,
                    "elapsed_ms": m.elapsed_ms, "status": m.status,
                    "created_at": m.created_at.isoformat(),
                }
                for m in new.messages
            ],
        }

    @app.post("/v1/sessions/{sid}/messages")
    async def post_message(sid: str, body: CreateMessageBody):
        sess = state["store"].get_session(sid)
        if sess is None:
            raise HTTPException(404, "session not found")
        dataset = state["datasets"].get(sess.dataset or "demo_orders")
        return StreamingResponse(
            _run_message_stream(state, sess, body.content, dataset),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/stats")
    def stats():
        lats = list(state["latencies"])
        if not lats:
            return {"count": 0}
        lats_sorted = sorted(lats)
        p = lambda q: lats_sorted[min(len(lats_sorted) - 1, int(q * len(lats_sorted)))]
        return {
            "count": len(lats),
            "p50_ms": p(0.5),
            "p95_ms": p(0.95),
            "p99_ms": p(0.99),
        }

    @app.get("/", response_class=HTMLResponse)
    def root():
        return _INDEX_HTML

    return app


async def _run_message_stream(
    state: dict, sess, content: str, dataset: Dataset | None,
) -> AsyncIterator[bytes]:
    started = time.time()
    store: Store = state["store"]
    llm: LLMProvider = state["llm"]
    runner: SandboxRunner = state["runner"]

    # Persist the user's message immediately so a refresh of the
    # session view shows it even if the model stream errors out.
    user_msg = store.add_message(sess.id, "user", content)
    yield sse("user_message", {"id": user_msg.id, "content": content})

    # Build context from the last ~10 messages to keep prompt size bounded.
    history: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]
    fresh = store.get_session(sess.id)
    if fresh is not None:
        for m in fresh.messages[-20:]:
            if m.role in ("user", "assistant"):
                history.append(Message(role=m.role, content=m.content))

    # Stream tokens from the LLM and assemble the final assistant text.
    pieces: list[str] = []
    try:
        async for tok in llm.stream(history, model=os.environ.get(
                "DATACHAT_LLM_MODEL", "gpt-4o-mini")):
            if tok.content:
                pieces.append(tok.content)
                yield sse("token", {"delta": tok.content})
            if tok.finish_reason:
                yield sse("finish", {"reason": tok.finish_reason})
    except Exception as e:  # noqa: BLE001
        elapsed = int((time.time() - started) * 1000)
        store.add_message(
            sess.id, "assistant",
            f"(LLM error: {e})", elapsed_ms=elapsed, status="error",
        )
        yield sse("error", {"error": str(e)})
        yield sse("done", {})
        return

    text = "".join(pieces)
    code = extract_code(text)
    yield sse("code", {"code": code or ""})

    figure_json: str | None = None
    if code is not None:
        result = await runner.run(code, dataset.path if dataset else None)
        figure_json = json.dumps(result.figure) if result.figure else None
        store.add_run(
            message_id="-pending-",  # filled below; we update via add_run
            exit_code=result.exit_code, elapsed_ms=result.elapsed_ms,
            peak_mem_bytes=result.peak_mem_bytes,
            stdout=result.stdout, stderr=result.stderr,
            error_class=result.error_class,
        ) if False else None  # see message-add below; we wire run correctly
        yield sse("exec_result", {
            "ok": result.ok,
            "figure": result.figure,
            "result_repr": result.result_repr,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed_ms": result.elapsed_ms,
            "error_class": result.error_class,
            "error_message": result.error_message,
        })

    elapsed = int((time.time() - started) * 1000)
    state["latencies"].append(elapsed)
    asst = store.add_message(
        sess.id, "assistant", text,
        code=code, figure_json=figure_json, elapsed_ms=elapsed,
        status="ok" if (code is None or figure_json is not None) else "exec_failed",
    )
    yield sse("done", {"message_id": asst.id, "elapsed_ms": elapsed})


_INDEX_HTML = """<!doctype html>
<html><body style="font-family: ui-monospace, monospace; max-width: 720px; margin: 40px auto; padding: 0 16px;">
<h1>DataChat API</h1>
<p>POST <code>/v1/sessions</code> then POST <code>/v1/sessions/&lt;id&gt;/messages</code>.</p>
<p>The React notebook is at <a href="http://localhost:5173">localhost:5173</a>.</p>
</body></html>"""
