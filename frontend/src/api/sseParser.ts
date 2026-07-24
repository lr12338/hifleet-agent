/**
 * Robust, framework-agnostic SSE parser for the dialogue test workbench.
 *
 * Handles `\n\n` and `\r\n\r\n` delimiters, cross-network chunk splits, multi-line
 * `data:`, `event:`, `id:` and comment (`:`) heartbeat lines, tail-unclosed
 * frames, UTF-8 across chunks, invalid-JSON downgrade to raw, duplicate event-id
 * de-duplication, and terminal `run.completed`/`run.cancelled`/`run.timeout`/
 * `run.failed` detection (with `incompleteStream` when the stream ends without a
 * terminal event).
 *
 * The renderer must consume only typed DebugEvent V1 payloads; this parser never
 * guesses that an arbitrary packet is an answer.
 */

export interface ParsedSSEEvent {
  event: string;
  data: unknown;
  raw: string;
  id?: string;
}

export const TERMINAL_DEBUG_TYPES = new Set([
  "run.completed",
  "run.cancelled",
  "run.timeout",
  "run.failed",
]);

/** Split a complete buffer into SSE frames on blank lines (\n\n or \r\n\r\n). */
export function splitFrames(buffer: string): { frames: string[]; rest: string } {
  const frames: string[] = [];
  let cursor = 0;
  // Match either \r\n\r\n or \n\n, preferring the earliest.
  const re = /(\r\n\r\n|\n\n)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(buffer)) !== null) {
    frames.push(buffer.slice(cursor, match.index));
    cursor = match.index + match[0].length;
    re.lastIndex = cursor;
  }
  return { frames, rest: buffer.slice(cursor) };
}

/** Parse one SSE frame into a ParsedSSEEvent. */
export function parseFrame(frame: string): ParsedSSEEvent | null {
  if (!frame.trim()) return null;
  let event = "message";
  const dataLines: string[] = [];
  let id: string | undefined;
  let hasData = false;
  for (const rawLine of frame.split(/\r?\n/)) {
    if (rawLine === "") continue;
    if (rawLine.startsWith(":")) continue; // comment / heartbeat
    if (rawLine.startsWith("event:")) {
      event = rawLine.slice(6).trim() || "message";
      continue;
    }
    if (rawLine.startsWith("id:")) {
      id = rawLine.slice(3).trim();
      continue;
    }
    if (rawLine.startsWith("data:")) {
      hasData = true;
      dataLines.push(rawLine.slice(5).replace(/^ /, ""));
      continue;
    }
    // Unknown field: ignore per SSE spec.
  }
  if (!hasData) return null;
  const raw = dataLines.join("\n");
  let data: unknown = raw;
  try {
    data = JSON.parse(raw);
  } catch {
    data = raw; // invalid JSON -> raw string
  }
  return { event, data, raw, id };
}

export interface SSEStreamResult {
  events: ParsedSSEEvent[];
  incompleteStream: boolean;
  terminalType: string | null;
}

/**
 * Consume a fetch Response as an SSE stream with full chunk handling.
 * Stops after a terminal run.* event. Detects `incompleteStream` when the body
 * ends without a terminal event.
 */
export async function consumeSSEStream(
  response: Response,
  handlers: {
    onEvent?: (event: ParsedSSEEvent) => void;
    onTerminal?: (event: ParsedSSEEvent) => void;
    onIncomplete?: () => void;
  },
  signal?: AbortSignal
): Promise<SSEStreamResult> {
  const events: ParsedSSEEvent[] = [];
  const seenIds = new Set<string>();
  let incompleteStream = false;
  let terminalType: string | null = null;

  const reader = response.body?.getReader();
  if (!reader) {
    return { events, incompleteStream: false, terminalType };
  }
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const dispatch = (ev: ParsedSSEEvent): boolean => {
    if (ev.id !== undefined) {
      if (seenIds.has(ev.id)) return false; // duplicate id de-dupe
      seenIds.add(ev.id);
    }
    events.push(ev);
    handlers.onEvent?.(ev);
    const type = typeof ev.data === "object" && ev.data !== null
      ? String((ev.data as Record<string, unknown>).type ?? "")
      : "";
    if (TERMINAL_DEBUG_TYPES.has(type)) {
      terminalType = type;
      handlers.onTerminal?.(ev);
      return true; // stop
    }
    return false;
  };

  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (signal?.aborted) {
        incompleteStream = true;
        break;
      }
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = splitFrames(buffer);
      buffer = rest;
      let stop = false;
      for (const frame of frames) {
        const ev = parseFrame(frame);
        if (ev && dispatch(ev)) {
          stop = true;
          break;
        }
      }
      if (stop) break;
    }
    // Flush trailing buffer (tail-unclosed frame): treat as incomplete.
    if (!terminalType && buffer.trim()) {
      const ev = parseFrame(buffer);
      if (ev) dispatch(ev);
      if (!terminalType) incompleteStream = true;
    }
    if (!terminalType && events.length > 0) {
      incompleteStream = true;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
  if (incompleteStream) handlers.onIncomplete?.();
  return { events, incompleteStream, terminalType };
}

/** Fetch an SSE endpoint and parse it, throwing on non-2xx. */
export async function fetchSSE(
  url: string,
  init: RequestInit,
  handlers: {
    onEvent?: (event: ParsedSSEEvent) => void;
    onTerminal?: (event: ParsedSSEEvent) => void;
    onIncomplete?: () => void;
  },
  signal?: AbortSignal
): Promise<SSEStreamResult> {
  const res = await fetch(url, { ...init, signal });
  if (!res.ok) {
    if (res.status === 401) throw new Error("UNAUTHORIZED");
    throw new Error(`Request failed: ${res.status}`);
  }
  return consumeSSEStream(res, handlers, signal);
}
