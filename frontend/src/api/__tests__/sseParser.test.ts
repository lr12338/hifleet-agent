import { describe, expect, it } from "vitest";
import { consumeSSEStream, parseFrame, splitFrames, TERMINAL_DEBUG_TYPES, type ParsedSSEEvent } from "../sseParser";

function makeResponse(chunks: Uint8Array[] | string[], contentType = "text/event-stream"): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) {
        controller.enqueue(typeof c === "string" ? encoder.encode(c) : c);
      }
      controller.close();
    }
  });
  return new Response(stream, { status: 200, headers: { "content-type": contentType } });
}

async function drain(chunks: Uint8Array[] | string[]) {
  const events: ParsedSSEEvent[] = [];
  const res = await consumeSSEStream(makeResponse(chunks), { onEvent: (e) => events.push(e) });
  return { ...res, events };
}

describe("splitFrames", () => {
  it("splits on \\n\\n", () => {
    const { frames, rest } = splitFrames("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    expect(frames).toHaveLength(2);
    expect(rest).toBe("");
  });

  it("splits on \\r\\n\\r\\n", () => {
    const { frames, rest } = splitFrames("event: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n");
    expect(frames).toHaveLength(2);
    expect(rest).toBe("");
  });

  it("keeps unclosed tail as rest", () => {
    const { frames, rest } = splitFrames("event: a\ndata: 1\n\nevent: b\ndata: 2");
    expect(frames).toHaveLength(1);
    expect(rest).toBe("event: b\ndata: 2");
  });
});

describe("parseFrame", () => {
  it("parses event + json data", () => {
    const ev = parseFrame('event: debug\ndata: {"type":"run.started","run_id":"r"}');
    expect(ev?.event).toBe("debug");
    expect(ev?.data).toEqual({ type: "run.started", run_id: "r" });
  });

  it("joins multi-line data", () => {
    const ev = parseFrame('event: debug\ndata: {"type":"run.started",\ndata: "run_id":"r"}');
    expect(ev?.data).toEqual({ type: "run.started", run_id: "r" });
  });

  it("downgrades invalid json to raw string", () => {
    const ev = parseFrame("event: debug\ndata: not-json");
    expect(ev?.data).toBe("not-json");
  });

  it("ignores comment heartbeat lines", () => {
    const ev = parseFrame(": heartbeat\nevent: debug\ndata: {\"type\":\"heartbeat\"}");
    expect(ev?.event).toBe("debug");
    expect(ev?.data).toEqual({ type: "heartbeat" });
  });

  it("captures id and defaults event to message", () => {
    const ev = parseFrame('id: 7\ndata: {"type":"answer.delta"}');
    expect(ev?.id).toBe("7");
    expect(ev?.event).toBe("message");
  });
});

describe("consumeSSEStream", () => {
  it("parses \\n\\n stream and terminates on run.completed", async () => {
    const chunks = [
      'event: debug\ndata: {"type":"run.started","run_id":"r"}\n\n',
      'event: debug\ndata: {"type":"answer.delta","data":{"delta":"Hi"}}\n\n',
      'event: debug\ndata: {"type":"run.completed"}\n\n'
    ];
    const { events, terminalType, incompleteStream } = await drain(chunks);
    expect(events).toHaveLength(3);
    expect(terminalType).toBe("run.completed");
    expect(incompleteStream).toBe(false);
  });

  it("parses \\r\\n\\r\\n stream", async () => {
    const chunks = [
      'event: debug\r\ndata: {"type":"run.started","run_id":"r"}\r\n\r\n',
      'event: debug\r\ndata: {"type":"answer.delta","data":{"delta":"Hi"}}\r\n\r\n',
      'event: debug\r\ndata: {"type":"run.completed"}\r\n\r\n'
    ];
    const { events, terminalType } = await drain(chunks);
    expect(events).toHaveLength(3);
    expect(terminalType).toBe("run.completed");
  });

  it("marks incomplete stream when no terminal event", async () => {
    const chunks = ['event: debug\ndata: {"type":"answer.delta","data":{"delta":"partial"}}\n\n'];
    const { incompleteStream, terminalType } = await drain(chunks);
    expect(terminalType).toBeNull();
    expect(incompleteStream).toBe(true);
  });

  it("handles UTF-8 chinese split across chunks", async () => {
    const head = 'event: debug\ndata: {"type":"answer.delta","data":{"delta":"';
    const tail = '"}}\n\n';
    const full = head + "数据" + tail;
    const bytes = new TextEncoder().encode(full);
    const mid = Math.floor(bytes.length / 2);
    const { events } = await drain([bytes.slice(0, mid), bytes.slice(mid)]);
    const data = events[0].data as { data: { delta: string } };
    expect(data.data.delta).toBe("数据");
  });

  it("de-duplicates duplicate event ids", async () => {
    const chunks = [
      'id: 1\nevent: debug\ndata: {"type":"run.started"}\n\n',
      'id: 1\nevent: debug\ndata: {"type":"answer.delta","data":{"delta":"x"}}\n\n',
      'event: debug\ndata: {"type":"run.completed"}\n\n'
    ];
    const { events } = await drain(chunks);
    expect(events).toHaveLength(2); // duplicate id 1 dropped
  });

  it("marks incomplete on abort", async () => {
    const ctrl = new AbortController();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: debug\ndata: {"type":"answer.delta"}\n\n'));
        // never close; abort will fire
      }
    });
    const res = new Response(stream, { status: 200 });
    const p = consumeSSEStream(res, {}, ctrl.signal);
    ctrl.abort();
    const result = await p;
    expect(result.incompleteStream).toBe(true);
  });

  it("terminal types set is complete", () => {
    expect(TERMINAL_DEBUG_TYPES.has("run.completed")).toBe(true);
    expect(TERMINAL_DEBUG_TYPES.has("run.cancelled")).toBe(true);
    expect(TERMINAL_DEBUG_TYPES.has("run.timeout")).toBe(true);
    expect(TERMINAL_DEBUG_TYPES.has("run.failed")).toBe(true);
  });
});
