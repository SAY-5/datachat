"""Module-level FastAPI app for `uvicorn app.api.factory:app`.

`build_app()` is preferred for tests and embedding; this thin wrapper exists
so production deployments can point uvicorn at a stable importable name.
"""

from __future__ import annotations

from .app import build_app

app = build_app()
