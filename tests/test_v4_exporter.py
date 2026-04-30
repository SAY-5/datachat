from __future__ import annotations

from app.store.exporter import ChartRow, to_csv, to_jsonl


def _r(chart_id: str = "c1") -> ChartRow:
    return ChartRow(
        chart_id=chart_id,
        session_id="s1",
        query="SELECT 1",
        chart_type="bar",
        created_at_iso="2026-04-30T10:00:00Z",
    )


def test_csv_emits_header_plus_rows() -> None:
    out = to_csv([_r("a"), _r("b")])
    lines = out.strip().split("\n")
    assert lines[0].startswith("chart_id,")
    assert len(lines) == 3


def test_csv_escapes_embedded_commas() -> None:
    r = ChartRow(chart_id="c1", session_id="s1",
                 query="SELECT a,b FROM t", chart_type="bar",
                 created_at_iso="2026-04-30T10:00:00Z")
    out = to_csv([r])
    # The query contains a comma; it must be quoted.
    assert '"SELECT a,b FROM t"' in out


def test_jsonl_one_record_per_line() -> None:
    out = to_jsonl([_r("a"), _r("b")])
    lines = out.strip().split("\n")
    assert len(lines) == 2
    import json
    obj = json.loads(lines[0])
    assert obj["chart_id"] == "a"


def test_empty_inputs_round_trip() -> None:
    assert to_csv([]).strip().split("\n")[0].startswith("chart_id")
    assert to_jsonl([]) == ""
