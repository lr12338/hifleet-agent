import { describe, expect, it } from "vitest";
import { buildAnswerFromDeltas, getDebugV1Type, isTerminalType, pairToolEvents, redactForDisplay, sanitizeSignedUrl } from "../debugEvent";

describe("getDebugV1Type", () => {
  it("reads type from V1 payload", () => {
    expect(getDebugV1Type({ type: "answer.delta", data: { delta: "x" } })).toBe("answer.delta");
    expect(getDebugV1Type({ foo: 1 })).toBe("");
  });
});

describe("isTerminalType", () => {
  it("flags run terminal types", () => {
    expect(isTerminalType("run.completed")).toBe(true);
    expect(isTerminalType("run.cancelled")).toBe(true);
    expect(isTerminalType("run.timeout")).toBe(true);
    expect(isTerminalType("run.failed")).toBe(true);
    expect(isTerminalType("answer.delta")).toBe(false);
  });
});

describe("buildAnswerFromDeltas", () => {
  it("concatenates deltas in order", () => {
    const events = [
      { data: { type: "answer.delta", data: { delta: "Hello " } } },
      { data: { type: "answer.delta", data: { delta: "world" } } },
      { data: { type: "answer.completed", data: { answer: "Hello world" } } }
    ];
    expect(buildAnswerFromDeltas(events)).toBe("Hello world");
  });
  it("returns empty for no deltas", () => {
    expect(buildAnswerFromDeltas([{ data: { type: "run.started" } }])).toBe("");
  });
});

describe("pairToolEvents", () => {
  it("pairs started/completed/failed by call_id", () => {
    const events = [
      { data: { type: "tool.started", call_id: "c1", data: { tool_name: "local_kb_search" } } },
      { data: { type: "tool.completed", call_id: "c1", duration_ms: 42, data: { tool_name: "local_kb_search" } } },
      { data: { type: "tool.started", call_id: "c2", data: { tool_name: "inspect_media" } } },
      { data: { type: "tool.failed", call_id: "c2", data: { tool_name: "inspect_media" } } }
    ];
    const cards = pairToolEvents(events);
    expect(cards).toHaveLength(2);
    expect(cards[0]).toEqual({ id: "c1", name: "local_kb_search", status: "completed", durationMs: 42 });
    expect(cards[1]).toEqual({ id: "c2", name: "inspect_media", status: "failed" });
  });
  it("does not double-count request/response as two tools", () => {
    const events = [
      { data: { type: "tool.started", call_id: "c1", data: { tool_name: "web_search" } } },
      { data: { type: "tool.completed", call_id: "c1", data: { tool_name: "web_search" } } }
    ];
    expect(pairToolEvents(events)).toHaveLength(1);
  });
});

describe("redactForDisplay", () => {
  it("redacts sensitive keys", () => {
    const out = redactForDisplay({ api_key: "sk-x", authorization: "Bearer y", text: "ok" }) as Record<string, unknown>;
    expect(out.api_key).toBe("***");
    expect(out.authorization).toBe("***");
    expect(out.text).toBe("ok");
  });
  it("redacts inline secrets in strings", () => {
    const out = redactForDisplay("Authorization: Bearer secret123") as string;
    expect(out).not.toContain("secret123");
  });
  it("does not render raw_provider_event as answer", () => {
    // raw_provider_event must never be treated as an answer delta
    const events = [{ data: { type: "raw_provider_event", data: { output_text: "fake" } } }];
    expect(buildAnswerFromDeltas(events)).toBe("");
  });
});

describe("sanitizeSignedUrl", () => {
  it("redacts signed url query params", () => {
    const url = "https://b.oss.com/k?Expires=1&Signature=SECRET&file=x";
    const out = sanitizeSignedUrl(url);
    expect(out).toContain("Signature=***");
    expect(out).not.toContain("SECRET");
    expect(out).toContain("file=x");
  });
});
