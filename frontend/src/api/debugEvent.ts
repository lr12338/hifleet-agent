/**
 * Pure helpers for interpreting DebugEvent V1 payloads on the client.
 * The page renders strictly by `type`; these helpers centralize the logic so it
 * is unit-testable without rendering the full component.
 */
import { TERMINAL_DEBUG_TYPES } from "./sseParser";

export type DebugV1Type =
  | "run.started"
  | "input.normalized"
  | "route.selected"
  | "phase.started"
  | "phase.completed"
  | "reasoning.summary"
  | "tool.started"
  | "tool.arguments.delta"
  | "tool.completed"
  | "tool.failed"
  | "evidence.summary"
  | "guard.result"
  | "answer.started"
  | "answer.delta"
  | "answer.completed"
  | "run.completed"
  | "run.cancelled"
  | "run.timeout"
  | "run.failed"
  | "heartbeat"
  | "raw_provider_event";

export function getDebugV1Type(data: unknown): DebugV1Type | "" {
  if (data && typeof data === "object" && "type" in data) {
    const t = String((data as Record<string, unknown>).type ?? "");
    return t as DebugV1Type;
  }
  return "";
}

export function isTerminalType(type: string): boolean {
  return TERMINAL_DEBUG_TYPES.has(type);
}

/** Assemble the full answer from a sequence of answer.delta events. */
export function buildAnswerFromDeltas(events: { data: unknown }[]): string {
  let out = "";
  for (const ev of events) {
    const d = ev.data as { data?: { delta?: string } } | undefined;
    const delta = d?.data?.delta;
    if (typeof delta === "string") out += delta;
  }
  return out;
}

export interface ToolCard {
  id: string;
  name: string;
  status: "started" | "completed" | "failed" | "unknown";
  durationMs?: number;
}

/** Pair tool.started/completed/failed by call_id into tool cards. */
export function pairToolEvents(events: { data: unknown }[]): ToolCard[] {
  const cards: Record<string, ToolCard> = {};
  const order: string[] = [];
  for (const ev of events) {
    const d = ev.data as Record<string, unknown> | undefined;
    if (!d) continue;
    const type = getDebugV1Type(d);
    const callId = String(d.call_id ?? "");
    if (type === "tool.started") {
      const name = String((d.data as { tool_name?: string })?.tool_name ?? "unknown");
      if (!cards[callId]) {
        cards[callId] = { id: callId, name, status: "started" };
        order.push(callId);
      }
    } else if (type === "tool.completed") {
      if (!cards[callId]) {
        cards[callId] = { id: callId, name: String((d.data as { tool_name?: string })?.tool_name ?? "unknown"), status: "unknown" };
        order.push(callId);
      }
      cards[callId].status = "completed";
      if (typeof d.duration_ms === "number") cards[callId].durationMs = d.duration_ms;
    } else if (type === "tool.failed") {
      if (!cards[callId]) {
        cards[callId] = { id: callId, name: String((d.data as { tool_name?: string })?.tool_name ?? "unknown"), status: "unknown" };
        order.push(callId);
      }
      cards[callId].status = "failed";
    }
  }
  return order.map((id) => cards[id]);
}

const SENSITIVE_KEYS = /^(api[_-]?key|authorization|cookie|token|secret|password|x-admin-api-key|signature)$/i;

/**
 * Redact sensitive fields before rendering raw payloads. Mirrors the backend
 * redaction so the UI never displays secrets even if they slipped into data.
 */
export function redactForDisplay(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactForDisplay);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = SENSITIVE_KEYS.test(k) ? "***" : redactForDisplay(v);
    }
    return out;
  }
  if (typeof value === "string") {
    return value
      .replace(/(authorization|api[_-]?key|secret|token|signature|x-amz-signature|x-amz-credential|x-oss-signature)\s*[:=]\s*[^\s,;}&]+(\s+[^\s,;}&]+)*/gi, "$1=***")
      .replace(/\bBearer\s+[^\s,;}&]+/gi, "Bearer=***");
  }
  return value;
}

/** A signed URL's query params must not be rendered; keep host/path only. */
export function sanitizeSignedUrl(url: string): string {
  if (!url || !url.includes("?")) return url;
  const [base, query] = url.split("?", 1);
  const parts = (url.slice(base.length + 1) || "").split("&").map((pair) => {
    const key = pair.includes("=") ? pair.split("=", 1)[0] : pair;
    return /^(signature|sig|x-amz-signature|x-amz-credential|x-oss-signature|accesskeyid|token)$/i.test(key) ? `${key}=***` : pair;
  });
  return `${base}?${parts.join("&")}`;
}
