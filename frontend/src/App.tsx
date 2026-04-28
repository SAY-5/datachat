import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  api,
  sendMessage,
  type DatasetSummary,
  type PersistedMessage,
  type SessionDetail,
  type SessionSummary,
  type Stats,
  type StreamEvent,
} from "./lib/api.js";
import { FigurePlot } from "./components/FigurePlot.js";

const SUGGESTIONS = [
  "How many rows are in this dataset?",
  "Top 5 products by revenue",
  "Distribution of order values",
  "Monthly revenue trend",
  "Summary statistics",
];

interface UITurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  code?: string | null;
  figure?: unknown | null;
  resultRepr?: string | null;
  stdout?: string | null;
  stderr?: string | null;
  elapsedMs?: number | null;
  errorClass?: string | null;
  errorMessage?: string | null;
  status?: "pending" | "ok" | "err";
}

function persistedToUI(m: PersistedMessage): UITurn {
  return {
    id: m.id,
    role: m.role === "assistant" ? "assistant" : "user",
    text: m.content,
    code: m.code,
    figure: m.figure_json ? safeParse(m.figure_json) : null,
    elapsedMs: m.elapsed_ms,
    status: m.status === "error" ? "err" : "ok",
  };
}

function safeParse(s: string): unknown | null {
  try {
    return JSON.parse(s) as unknown;
  } catch {
    return null;
  }
}

function fmtKicker(idx: number, role: "user" | "assistant"): string {
  const n = String(idx + 1).padStart(2, "0");
  const tag = role === "user" ? "INQUIRY" : "ANALYSIS";
  return `${n} — ${tag}`;
}

function fmtDate(s: string): string {
  try {
    const d = new Date(s);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return s;
  }
}

function shortTitle(t: string | null, fallback: string): string {
  if (!t) return fallback;
  return t.length > 48 ? t.slice(0, 47) + "…" : t;
}

export function App() {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<UITurn[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [pickedDataset, setPickedDataset] = useState<string>("demo_orders");
  const threadRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ---- bootstrap ----------------------------------------------------------
  useEffect(() => {
    void (async () => {
      try {
        const [ds, ss, st] = await Promise.all([
          api.datasets(),
          api.listSessions(),
          api.stats().catch(() => ({ count: 0 } as Stats)),
        ]);
        setDatasets(ds);
        setSessions(ss);
        setStats(st);
        const first = ss[0];
        if (first) {
          setActiveId(first.id);
          setPickedDataset(first.dataset ?? "demo_orders");
        } else if (ds[0]) {
          setPickedDataset(ds[0].id);
        }
      } catch (err) {
        console.error("bootstrap failed", err);
      }
    })();
  }, []);

  // ---- load active session detail ----------------------------------------
  useEffect(() => {
    if (!activeId) {
      setTurns([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const s: SessionDetail = await api.getSession(activeId);
        if (cancelled) return;
        setPickedDataset(s.dataset ?? "demo_orders");
        setTurns(s.messages.filter((m) => m.role !== "system").map(persistedToUI));
      } catch (err) {
        console.error("load session failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // ---- autoscroll ---------------------------------------------------------
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns, streaming]);

  // ---- session helpers ----------------------------------------------------
  const createSession = useCallback(
    async (dataset: string, openImmediately = true): Promise<SessionSummary> => {
      const s = await api.createSession(dataset);
      setSessions((prev) => [s, ...prev]);
      if (openImmediately) setActiveId(s.id);
      return s;
    },
    [],
  );

  const ensureSession = useCallback(async (): Promise<string> => {
    if (activeId) return activeId;
    const s = await createSession(pickedDataset, true);
    return s.id;
  }, [activeId, pickedDataset, createSession]);

  // ---- streaming send -----------------------------------------------------
  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;
      const sid = await ensureSession();

      // optimistic placeholders.
      const tempUserId = `u-${Date.now()}`;
      const tempAsstId = `a-${Date.now()}`;
      setTurns((prev) => [
        ...prev,
        { id: tempUserId, role: "user", text: trimmed },
        {
          id: tempAsstId,
          role: "assistant",
          text: "",
          status: "pending",
        },
      ]);
      setDraft("");
      setStreaming(true);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      let asstAccum = "";
      let asstCode: string | null = null;
      let asstFigure: unknown | null = null;
      let lastResult: {
        stdout?: string; stderr?: string; resultRepr?: string | null;
        elapsedMs?: number; errorClass?: string | null; errorMessage?: string | null;
      } = {};

      const update = (patch: Partial<UITurn>) =>
        setTurns((prev) =>
          prev.map((t) => (t.id === tempAsstId ? { ...t, ...patch } : t)),
        );

      try {
        await sendMessage(sid, trimmed, {
          signal: ctrl.signal,
          onEvent: (ev: StreamEvent) => {
            switch (ev.type) {
              case "user_message":
                setTurns((prev) =>
                  prev.map((t) =>
                    t.id === tempUserId ? { ...t, id: ev.id, text: ev.content } : t,
                  ),
                );
                break;
              case "token":
                asstAccum += ev.delta;
                update({ text: asstAccum });
                break;
              case "code":
                asstCode = ev.code || null;
                update({ code: asstCode });
                break;
              case "exec_result":
                asstFigure = ev.payload.figure ?? null;
                lastResult = {
                  stdout: ev.payload.stdout,
                  stderr: ev.payload.stderr,
                  resultRepr: ev.payload.result_repr,
                  elapsedMs: ev.payload.elapsed_ms,
                  errorClass: ev.payload.error_class,
                  errorMessage: ev.payload.error_message,
                };
                update({
                  figure: asstFigure,
                  resultRepr: lastResult.resultRepr ?? null,
                  stdout: lastResult.stdout ?? null,
                  stderr: lastResult.stderr ?? null,
                  elapsedMs: lastResult.elapsedMs ?? null,
                  errorClass: lastResult.errorClass ?? null,
                  errorMessage: lastResult.errorMessage ?? null,
                  status: ev.payload.ok ? "ok" : "err",
                });
                break;
              case "error":
                update({
                  text: asstAccum || `(error) ${ev.error}`,
                  errorMessage: ev.error,
                  status: "err",
                });
                break;
              case "done":
                if (typeof ev.elapsed_ms === "number") {
                  update({ elapsedMs: ev.elapsed_ms });
                }
                break;
              default:
                break;
            }
          },
        });
      } catch (err) {
        if ((err as { name?: string }).name !== "AbortError") {
          update({
            text: asstAccum || `(stream error) ${(err as Error).message}`,
            status: "err",
          });
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
        // Refresh stats; cheap.
        api.stats().then(setStats).catch(() => undefined);
      }
    },
    [ensureSession, streaming],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // ---- handlers -----------------------------------------------------------
  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void send(draft);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(draft);
    }
  };

  const onPickSuggestion = (s: string) => {
    setDraft(s);
    void send(s);
  };

  const newSession = async () => {
    await createSession(pickedDataset, true);
    setDraft("");
  };

  // ---- derived ------------------------------------------------------------
  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeId) ?? null,
    [sessions, activeId],
  );

  const datasetOptions = datasets.length > 0 ? datasets : [
    { id: "demo_orders", rows: 0, columns: [] },
  ];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            data<em>chat</em>
          </div>
          <div className="brand-volume">Vol. I · No. 3</div>
        </div>
        <div className="thread-meta">
          {activeSession
            ? <>An inquiry into <em>{activeSession.dataset ?? "demo_orders"}</em>.</>
            : <>An inquiry into <em>{pickedDataset}</em>.</>}
        </div>
        <div className="topbar-meta">
          <span>p50 <b>{stats?.p50_ms ?? "—"}{stats?.p50_ms ? "ms" : ""}</b></span>
          <span>p95 <b>{stats?.p95_ms ?? "—"}{stats?.p95_ms ? "ms" : ""}</b></span>
          <span>turns <b>{stats?.count ?? 0}</b></span>
        </div>
      </header>

      <main>
        <aside className="sidebar">
          <div className="sidebar-head">
            <span>Sessions</span>
            <button className="btn primary" onClick={() => void newSession()}>
              + New
            </button>
          </div>
          <ul className="session-list">
            {sessions.map((s, i) => (
              <li
                key={s.id}
                className={s.id === activeId ? "active" : ""}
                onClick={() => setActiveId(s.id)}
              >
                <span className="title">
                  {shortTitle(s.title, `Session №${sessions.length - i}`)}
                </span>
                <span className="meta">
                  {(s.dataset ?? "—").toUpperCase()} · {fmtDate(s.created_at)}
                </span>
              </li>
            ))}
            {sessions.length === 0 && (
              <li onClick={() => void newSession()}>
                <span className="title">No sessions yet</span>
                <span className="meta">CLICK + NEW TO BEGIN</span>
              </li>
            )}
          </ul>
        </aside>

        <section className="chat">
          <div className="thread" ref={threadRef}>
            {turns.length === 0 ? (
              <div className="thread-empty">
                <div className="eyebrow">Issue №3 · Spring 2025</div>
                <h1>
                  An <em>inquiry</em><br />into your data.
                </h1>
                <p>
                  Ask in plain English. The notebook will draft Python,
                  execute it in a sealed sandbox, and typeset the answer.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => onPickSuggestion(s)}
                      disabled={streaming}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              turns.map((t, i) => (
                <Turn key={t.id} turn={t} kicker={fmtKicker(i, t.role)} />
              ))
            )}
            {streaming && <div className="loading-bar" />}
          </div>

          <form className="composer" onSubmit={onSubmit}>
            <div className="composer-inner">
              <textarea
                placeholder="Pose a question to the dataset…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={streaming}
              />
              <div className="composer-row">
                <label className="dataset-pick">
                  <span>DATASET</span>
                  <select
                    value={pickedDataset}
                    onChange={(e) => setPickedDataset(e.target.value)}
                    disabled={!!activeSession || streaming}
                  >
                    {datasetOptions.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.id}{d.rows > 0 ? ` · ${d.rows.toLocaleString()} rows` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <div style={{ display: "flex", gap: 8 }}>
                  {streaming && (
                    <button type="button" className="btn" onClick={cancel}>
                      Stop
                    </button>
                  )}
                  <button
                    type="submit"
                    className="btn primary"
                    disabled={streaming || draft.trim().length === 0}
                  >
                    Submit ↵
                  </button>
                </div>
              </div>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}

function Turn({ turn, kicker }: { turn: UITurn; kicker: string }) {
  const isUser = turn.role === "user";
  return (
    <article className={`turn ${turn.role}`} data-kicker={kicker}>
      <div className="body">
        {turn.text || (turn.status === "pending" ? "…" : "")}
      </div>
      {!isUser && turn.code && (
        <pre className="code-block">{turn.code}</pre>
      )}
      {!isUser && (!!turn.figure || !!turn.resultRepr || typeof turn.elapsedMs === "number") && (
        <div className="exec-meta">
          {turn.status === "ok" && <span className="pill ok">EXEC OK</span>}
          {turn.status === "err" && <span className="pill err">EXEC ERR</span>}
          {typeof turn.elapsedMs === "number" && (
            <span>{turn.elapsedMs} ms</span>
          )}
          {!!turn.resultRepr && !turn.figure && (
            <span title={turn.resultRepr}>
              result · <code>{truncate(turn.resultRepr, 80)}</code>
            </span>
          )}
        </div>
      )}
      {!isUser && !!turn.figure && (
        <div className="figure-wrap">
          <FigurePlot figure={turn.figure} />
        </div>
      )}
      {!isUser && turn.errorMessage && (
        <pre className="error-box">
          {turn.errorClass ? `${turn.errorClass}: ` : ""}
          {turn.errorMessage}
          {turn.stderr ? `\n\n${turn.stderr}` : ""}
        </pre>
      )}
    </article>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
