import type { AgentErrorItem, ApiCallItem, DashboardSummary, LogStats, SessionSummaryItem, ToolInvocationItem } from "../types";
import { getAdminApiKey } from "../auth/adminAuth";

export interface LogListResponse {
  total: number;
  page: number;
  page_size: number;
  items: ApiCallItem[];
  stats: LogStats;
}

export interface LogDetailResponse {
  api_call: ApiCallItem | null;
  tool_invocations: ToolInvocationItem[];
  errors: AgentErrorItem[];
  summary?: Record<string, unknown>;
  trace?: Array<{ type: string; created_at?: string; label: string; payload: unknown }>;
}

const jsonHeaders = { "Content-Type": "application/json" };

function buildHeaders(extra?: HeadersInit): HeadersInit {
  const apiKey = getAdminApiKey();
  const headers: Record<string, string> = {
    ...jsonHeaders
  };
  if (apiKey) {
    headers["x-admin-api-key"] = apiKey;
  }
  if (extra && !(extra instanceof Headers)) {
    Object.assign(headers, extra as Record<string, string>);
  }
  return headers;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const requestInit: RequestInit = { ...init };
  requestInit.headers = buildHeaders(init?.headers);
  const res = await fetch(url, requestInit);
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("UNAUTHORIZED");
    }
    throw new Error(`Request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function fetchHealth() {
  return requestJson<{ status: string; message: string }>("/health");
}

export async function fetchLogs(params: URLSearchParams) {
  return requestJson<LogListResponse>(`/admin/logs?${params.toString()}`);
}

export async function fetchLogDetail(runId: string) {
  return requestJson<LogDetailResponse>(`/admin/logs/${encodeURIComponent(runId)}`);
}

export async function fetchSessionTimeline(sessionId: string) {
  return requestJson<{ session_id: string; user_id?: string; source_channel?: string; agent_profile?: string; summary?: Record<string, unknown>; calls: ApiCallItem[] }>(
    `/admin/sessions/${encodeURIComponent(sessionId)}`
  );
}

export async function fetchSessionSummaries(params: URLSearchParams) {
  return requestJson<{ total: number; page: number; page_size: number; items: SessionSummaryItem[] }>(
    `/admin/sessions?${params.toString()}`
  );
}

export async function fetchDashboardSummary(params: URLSearchParams) {
  return requestJson<DashboardSummary>(`/admin/dashboard/summary?${params.toString()}`);
}

export async function runTest(payload: {
  endpoint: "/run" | "/stream_run";
  payload: Record<string, unknown>;
  run_id?: string;
  stream?: boolean;
}) {
  return requestJson<Record<string, unknown>>("/admin/test/run", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export interface UploadedAttachment {
  bucket: string;
  key: string;
  url: string;
  content_type: string;
  size: number;
  etag?: string;
}

export async function uploadAdminAttachment(file: File): Promise<UploadedAttachment> {
  const formData = new FormData();
  formData.append("file", file);
  const apiKey = getAdminApiKey();
  const headers: Record<string, string> = {};
  if (apiKey) {
    headers["x-admin-api-key"] = apiKey;
  }
  const res = await fetch("/admin/files/upload", {
    method: "POST",
    headers,
    body: formData
  });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("UNAUTHORIZED");
    }
    const text = await res.text();
    throw new Error(`Upload failed: ${res.status} ${text}`);
  }
  return (await res.json()) as UploadedAttachment;
}

export interface StreamRunEvent {
  event: string;
  data: unknown;
  raw: string;
}

export interface PersistedChatDebugSession {
  session_key: string;
  title: string;
  status: "running" | "ended";
  meta_session_id: string;
  user_id: string;
  source_channel: string;
  model: string;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

async function consumeEventStream(
  url: string,
  payload: Record<string, unknown>,
  handlers: {
    onEvent?: (event: StreamRunEvent) => void;
    onDone?: () => void;
  },
  signal?: AbortSignal
) {
  const res = await fetch(url, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify(payload),
    signal
  });

  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("UNAUTHORIZED");
    }
    throw new Error(`Request failed: ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("Empty stream body");
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const emitBlock = (block: string) => {
    if (!block.trim()) return;
    const lines = block.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim() || "message";
        continue;
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (!dataLines.length) return;
    const raw = dataLines.join("\n");
    let data: unknown = raw;
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
    handlers.onEvent?.({ event: eventName, data, raw });
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let splitIndex = buffer.indexOf("\n\n");
    while (splitIndex >= 0) {
      const block = buffer.slice(0, splitIndex);
      emitBlock(block);
      buffer = buffer.slice(splitIndex + 2);
      splitIndex = buffer.indexOf("\n\n");
    }
  }

  const tail = buffer.trim();
  if (tail) {
    emitBlock(tail);
  }

  handlers.onDone?.();
}

export async function streamTestRun(
  payload: Record<string, unknown>,
  handlers: {
    onEvent?: (event: StreamRunEvent) => void;
    onDone?: () => void;
  },
  signal?: AbortSignal,
  runId?: string
) {
  return consumeEventStream(
    "/admin/test/run",
    {
      endpoint: "/stream_run",
      stream: true,
      run_id: runId,
      payload
    },
    handlers,
    signal
  );
}

export async function streamArkChat(
  payload: Record<string, unknown>,
  handlers: {
    onEvent?: (event: StreamRunEvent) => void;
    onDone?: () => void;
  },
  signal?: AbortSignal
) {
  return consumeEventStream("/admin/ark/chat", payload, handlers, signal);
}

export async function fetchChatDebugSessions(limit = 20) {
  return requestJson<{ items: PersistedChatDebugSession[] }>(`/admin/chat-debug/sessions?limit=${limit}`);
}

export async function saveChatDebugSession(sessionKey: string, payload: Record<string, unknown>) {
  return requestJson<{ ok: boolean; session_key: string }>(`/admin/chat-debug/sessions/${encodeURIComponent(sessionKey)}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function deleteChatDebugSession(sessionKey: string) {
  return requestJson<{ ok: boolean; session_key: string }>(`/admin/chat-debug/sessions/${encodeURIComponent(sessionKey)}`, {
    method: "DELETE"
  });
}
