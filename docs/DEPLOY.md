# DataChat — Deployment Guide

DataChat ships as a single-process FastAPI binary serving both the JSON API
and the static React notebook. Default storage is SQLite for an instant
local start; a Postgres DSN swaps that out without code changes.

## Quickstart (local, mock LLM)

```bash
git clone <fork>
cd datachat
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.api.factory:app --reload --port 8080
# in a second terminal
cd frontend && npm install && npm run dev
# open http://localhost:5173
```

## Docker (single host)

```bash
docker compose up --build -d
# UI:    http://localhost:8080/         (built React assets served by FastAPI)
# API:   http://localhost:8080/v1/...
# Stats: http://localhost:8080/v1/stats
```

The compose file boots Postgres on port 5433 and DataChat on 8080. Set
`DATACHAT_LLM_PROVIDER=openai` and `OPENAI_API_KEY=...` in the environment
to flip from the deterministic mock provider to gpt-4o-mini.

## Configuration

| env var                  | default                          | meaning |
|--------------------------|----------------------------------|---------|
| `DATACHAT_DSN`           | `sqlite:///./datachat.db`        | SQLAlchemy URL — Postgres recommended in prod |
| `DATACHAT_DATA_DIR`      | `./data`                         | Where datasets and the demo CSV live |
| `DATACHAT_CORS`          | `http://localhost:5173`          | Comma-separated origins |
| `DATACHAT_LLM_PROVIDER`  | `mock`                           | `mock` or `openai` |
| `DATACHAT_LLM_MODEL`     | `gpt-4o-mini`                    | Forwarded to the OpenAI provider |
| `OPENAI_API_KEY`         | unset                            | Required when provider is `openai` |
| `DATACHAT_SANDBOX_CPU`   | `5`                              | Per-run CPU seconds |
| `DATACHAT_SANDBOX_MEM`   | `536870912`                      | Per-run virtual address space (bytes) |
| `DATACHAT_SANDBOX_WALL`  | `10.0`                           | Wall-clock kill (seconds) |

## Production checklist

- **Run on Linux**, not macOS, so `RLIMIT_AS` is actually enforced. The
  sandbox layered defenses still hold on macOS, but the memory cap is
  best-effort there — see `ARCHITECTURE.md § Sandbox model`.
- **Pin a reverse proxy** (Caddy, nginx, traefik). Set the upstream to
  `proxy_buffering off` so SSE streams aren't held back. Compose ships
  `X-Accel-Buffering: no` headers so nginx is fine OOTB.
- **Persist `/srv/data`.** The demo CSV is regenerated lazily, but if you
  upload custom datasets they live there.
- **Postgres pool tuning.** `engine_args = {pool_size: 10, max_overflow: 20}`
  is reasonable for a single replica handling 50 RPS of chat traffic.
- **Sandbox container.** If you really care about isolation, run DataChat
  inside its own container *and* configure `--cap-drop=ALL --read-only
  --tmpfs /tmp:exec`. The sandbox is a defense in depth, not a substitute
  for a container boundary.
- **Observability.** `/v1/stats` exposes p50/p95/p99 over the last 512
  messages. Scrape it with Prometheus' `blackbox_exporter` or wire a tiny
  Prom adapter (we ship one in `app/api/metrics.py` if you uncomment the
  router).

## Operational runbook

| Symptom                              | Most likely cause | Fix |
|--------------------------------------|-------------------|-----|
| `exec_result.ok=false, error_class=WallTimeout` for many users | Dataset too large, or LLM-generated code is doing N² over rows | Lower `DATACHAT_SANDBOX_WALL` or tune the system prompt to mention dataset size |
| SSE stream hangs at `token` and never reaches `done` | Reverse proxy is buffering | Disable buffering for `/v1/sessions/*/messages` |
| `psycopg.OperationalError: too many clients` | Postgres pool too small | Bump `pool_size` and `max_overflow`; check for leaked sessions in `app/store/db.py` |
| Sandbox import-blocked errors on harmless modules | Audit hook fired during a transitive lazy-import | Add the module to `_PREWARM` in `harness.py` |
| Disk full | `runs.stdout`/`stderr` retention | Run `DELETE FROM runs WHERE created_at < NOW() - INTERVAL '30 days'` weekly |

## Scaling notes

DataChat is shared-nothing per request. The two stateful backings are
Postgres and the on-disk `data/` directory. Run as many replicas as you
want behind a load balancer; sticky sessions are *not* required (each SSE
request is self-contained).

The dominant cost is the sandbox subprocess fork + pandas import (~150 ms
warm on a modern Linux box). For >100 RPS, pre-fork a pool of warm
harness processes that read scripts from a control fd. We've benchmarked
the warm-pool approach to 5× throughput; the patch is in `app/sandbox/
pool.py` (off by default).
