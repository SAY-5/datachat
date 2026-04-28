"""Deterministic mock LLM that streams pre-canned answers.

Used by the test suite + offline demo. Pattern matches the user's
question and emits a small assistant response that contains a
pandas/plotly code block whose output the sandbox can run end-to-end.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .base import LLMProvider, Message, StreamToken


@dataclass
class MockLLMProvider(LLMProvider):
    name: str = "mock"
    chunk_delay_ms: int = 5

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "mock",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamToken]:
        question = ""
        for m in reversed(messages):
            if m.role == "user":
                question = m.content
                break
        text = self._answer_for(question)
        # Stream in ~16-char chunks so the client sees real progressive output.
        for i in range(0, len(text), 16):
            await asyncio.sleep(self.chunk_delay_ms / 1000.0)
            yield StreamToken(content=text[i : i + 16])
        yield StreamToken(content="", finish_reason="stop")

    def _answer_for(self, q: str) -> str:
        ql = q.lower()
        if re.search(r"how many rows|row count|size of (the )?dataset", ql):
            return _CHART_TPL.format(
                blurb="The dataset has this many rows:",
                code=(
                    "result = len(df)\n"
                    "import plotly.graph_objects as go\n"
                    "fig = go.Figure(go.Indicator(mode='number', value=result,\n"
                    "    title={'text': 'rows'}))\n"
                ),
            )
        if re.search(r"top \d+|best \d+|highest", ql) and "rev" in ql:
            n = _extract_n(ql, default=5)
            return _CHART_TPL.format(
                blurb=f"Top {n} rows by revenue:",
                code=(
                    "import plotly.express as px\n"
                    f"top = df.nlargest({n}, 'revenue')\n"
                    "result = top.to_dict(orient='records')\n"
                    "fig = px.bar(top, x='product', y='revenue', title='Top by revenue')\n"
                ),
            )
        if re.search(r"distribution|histogram|spread", ql):
            return _CHART_TPL.format(
                blurb="Histogram of the numeric column:",
                code=(
                    "import plotly.express as px\n"
                    "num = df.select_dtypes('number').columns[0]\n"
                    "result = df[num].describe().to_dict()\n"
                    "fig = px.histogram(df, x=num, nbins=30,\n"
                    "    title=f'distribution of {num}')\n"
                ),
            )
        if re.search(r"by month|over time|trend|time series", ql):
            return _CHART_TPL.format(
                blurb="Monthly aggregation:",
                code=(
                    "import pandas as pd, plotly.express as px\n"
                    "date_col = next((c for c in df.columns if 'date' in c.lower()), None)\n"
                    "value_col = df.select_dtypes('number').columns[0]\n"
                    "tmp = df.assign(_d=pd.to_datetime(df[date_col]))\n"
                    "agg = tmp.groupby(tmp['_d'].dt.to_period('M'))[value_col].sum().reset_index()\n"
                    "agg['_d'] = agg['_d'].astype(str)\n"
                    "result = agg.to_dict(orient='records')\n"
                    "fig = px.line(agg, x='_d', y=value_col, title='monthly ' + value_col)\n"
                ),
            )
        if re.search(r"summary|describe|overview", ql):
            return _CHART_TPL.format(
                blurb="Summary statistics:",
                code=(
                    "result = df.describe().to_dict()\n"
                    "import plotly.graph_objects as go\n"
                    "vals = df.select_dtypes('number').iloc[:, 0]\n"
                    "fig = go.Figure(go.Box(y=vals, name=str(vals.name)))\n"
                ),
            )
        # Fallback: head() so the user always sees something.
        return _CHART_TPL.format(
            blurb="Here's the head of the dataset.",
            code=(
                "import plotly.express as px\n"
                "head = df.head(10)\n"
                "result = head.to_dict(orient='records')\n"
                "num_cols = head.select_dtypes('number').columns\n"
                "if len(num_cols) >= 2:\n"
                "    fig = px.scatter(head, x=num_cols[0], y=num_cols[1],\n"
                "        title=f'{num_cols[0]} vs {num_cols[1]}')\n"
                "else:\n"
                "    import plotly.graph_objects as go\n"
                "    fig = go.Figure(go.Table(\n"
                "        header={'values': list(head.columns)},\n"
                "        cells={'values': [head[c].astype(str).tolist() for c in head.columns]}))\n"
            ),
        )


def _extract_n(text: str, *, default: int) -> int:
    m = re.search(r"(?:top|best|highest) ?(\d+)", text)
    return int(m.group(1)) if m else default


_CHART_TPL = """\
{blurb}

```python
{code}```
"""
