"""v4: SQL-friendly export of saved-chart metadata.

The saved-chart gallery (v3) stores charts in-process. v4 adds a
flat-table exporter: walk every saved chart, emit a row per
(chart_id, query, chart_type, created_at). The output is
SQL-loader-friendly so a downstream warehouse pipeline (Snowflake
COPY, BigQuery load) can ingest it without an adapter.

We intentionally don't write directly to a warehouse here — that
couples the app to a specific cloud. Production runs this in a
nightly cron and uploads the file.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChartRow:
    chart_id: str
    session_id: str
    query: str
    chart_type: str
    created_at_iso: str


def to_csv(rows: Iterable[ChartRow]) -> str:
    """CSV with header row matching ChartRow fields. Escapes embedded
    commas + newlines via csv module (SQL loaders rely on this)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["chart_id", "session_id", "query", "chart_type", "created_at_iso"])
    for r in rows:
        writer.writerow([r.chart_id, r.session_id, r.query, r.chart_type, r.created_at_iso])
    return buf.getvalue()


def to_jsonl(rows: Iterable[ChartRow]) -> str:
    """JSON-line variant for warehouses that prefer JSON ingest
    (BigQuery, ClickHouse). One record per line, no array wrapper."""
    import json
    out: list[str] = []
    for r in rows:
        out.append(json.dumps({
            "chart_id": r.chart_id,
            "session_id": r.session_id,
            "query": r.query,
            "chart_type": r.chart_type,
            "created_at_iso": r.created_at_iso,
        }))
    return "\n".join(out) + ("\n" if out else "")
