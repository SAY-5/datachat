/* SSE consumer for /v1/sessions/{id}/messages and thin REST helpers. */

export interface DatasetSummary {
  id: string;
  rows: number;
  columns: string[];
}

export interface SessionSummary {
  id: string;
  dataset: string | null;
  title: string | null;
  created_at: string;
}

export interface PersistedMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  code: string | null;
  figure_json: string | null;
  elapsed_ms: number | null;
  status: string | null;
  created_at: string;
}

export interface SessionDetail extends SessionSummary {
  messages: PersistedMessage[];
  forked_from_session_id?: string | null;
  forked_at_message_id?: string | null;
}

export interface Stats {
  count: number;
  p50_ms?: number;
  p95_ms?: number;
  p99_ms?: number;
}

export interface ExecResultPayload {
  ok: boolean;
  figure: unknown | null;
  result_repr: string | null;
  stdout: string;
  stderr: string;
  elapsed_ms: number;
  error_class: string | null;
  error_message: string | null;
}

export type StreamEvent =
  | { type: "user_message"; id: string; content: string }
  | { type: "token"; delta: string }
  | { type: "finish"; reason: string }
  | { type: "code"; code: string }
  | { type: "exec_result"; payload: ExecResultPayload }
  | { type: "error"; error: string }
  | { type: "done"; message_id?: string; elapsed_ms?: number };

const JSON_HEADERS = { "Content-Type": "application/json" };

async function http<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await fetch(input, init);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText} — ${text.slice(0, 200)}`);
  }
  return (await r.json()) as T;
}

export const api = {
  health: () => http<{ ok: boolean; datasets: number }>("/healthz"),
  datasets: () =>
    http<{ items: DatasetSummary[] }>("/v1/datasets").then((r) => r.items),
  stats: () => http<Stats>("/v1/stats"),
  listSessions: () =>
    http<{ items: SessionSummary[] }>("/v1/sessions").then((r) => r.items),
  getSession: (id: string) => http<SessionDetail>(`/v1/sessions/${id}`),
  createSession: (dataset?: string, title?: string) =>
    http<SessionSummary>("/v1/sessions", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ dataset, title }),
    }),
  forkSession: (sourceId: string, anchorMessageId: string, title?: string) =>
    http<SessionDetail>(`/v1/sessions/${sourceId}/fork`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ anchor_message_id: anchorMessageId, title }),
    }),
};

export interface SendMessageOptions {
  signal?: AbortSignal;
  onEvent: (e: StreamEvent) => void;
}

/**
 * Streams a single chat turn. Reads /v1/sessions/{id}/messages as SSE
 * using fetch + ReadableStream. Yields parsed events to onEvent.
 */
export async function sendMessage(
  sessionId: string,
  content: string,
  opts: SendMessageOptions,
): Promise<void> {
  const res = await fetch(`/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ content }),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`stream open failed (${res.status}): ${text.slice(0, 200)}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseFrame(frame);
      if (ev) opts.onEvent(ev);
    }
  }
}

function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const raw of frame.split("\n")) {
    const line = raw.trimEnd();
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const valueRaw = colon === -1 ? "" : line.slice(colon + 1);
    const value = valueRaw.startsWith(" ") ? valueRaw.slice(1) : valueRaw;
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  let payload: Record<string, unknown> = {};
  try {
    payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
  } catch {
    return null;
  }
  switch (event) {
    case "user_message":
      return {
        type: "user_message",
        id: String(payload.id ?? ""),
        content: String(payload.content ?? ""),
      };
    case "token":
      return { type: "token", delta: String(payload.delta ?? "") };
    case "finish":
      return { type: "finish", reason: String(payload.reason ?? "") };
    case "code":
      return { type: "code", code: String(payload.code ?? "") };
    case "exec_result":
      return { type: "exec_result", payload: payload as unknown as ExecResultPayload };
    case "error":
      return { type: "error", error: String(payload.error ?? "unknown") };
    case "done":
      return {
        type: "done",
        message_id: payload.message_id ? String(payload.message_id) : undefined,
        elapsed_ms:
          typeof payload.elapsed_ms === "number" ? payload.elapsed_ms : undefined,
      };
    default:
      return null;
  }
}
