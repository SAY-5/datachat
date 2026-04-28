# DataChat

An AI-powered data analysis chat. Ask a question in English; the
assistant streams Python code; the backend executes it in a sandboxed
subprocess; the React UI renders the resulting Plotly chart inline.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the design writeup
(sandbox model, streaming protocol, performance targets).

## Quick start

```bash
pip install -e ".[dev]"
datachat serve --port 8080
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The mock LLM is the default — no API key
required. The 10k-row `demo_orders` dataset is seeded on first run.

For real OpenAI:

```bash
pip install -e ".[dev,openai]"
export OPENAI_API_KEY=...
DATACHAT_LLM=openai datachat serve
```

## Companion projects

Part of a seven-repo set:

- **[canvaslive](https://github.com/SAY-5/canvaslive)** — multiplayer OT whiteboard
- **[pluginforge](https://github.com/SAY-5/pluginforge)** — Web Worker plugin sandbox
- **[agentlab](https://github.com/SAY-5/agentlab)** — AI agent eval harness
- **[payflow](https://github.com/SAY-5/payflow)** — payments API
- **[queryflow](https://github.com/SAY-5/queryflow)** — natural-language SQL
- **[datachat](https://github.com/SAY-5/datachat)** — you're here. AI data analysis.
- **[distributedkv](https://github.com/SAY-5/distributedkv)** — sharded KV with Raft

## License

MIT.
