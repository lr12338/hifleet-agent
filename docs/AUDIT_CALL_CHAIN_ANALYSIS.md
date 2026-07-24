# 对话测试工作台审计：调用链与事件来源分析

> 审计基线：分支 `codex/shared-skills-v2`，HEAD `f406ae7`（与 `origin` 一致）。
> 审计范围：`/run`、`/stream_run`、admin 测试代理、两条客服链路事件来源、前端 SSE 解析。
> 本文件为改造前审计输出，不包含任何页面改动。

## 1. 当前 `/run` 调用链

```
浏览器 ChatDebugPage -> POST /admin/test/run (admin_router)
  -> AdminTestRunRequest{endpoint="/run", payload, target_agent_url?, timeout_s, stream=false}
  -> service.proxy_test_run(req)
       base_url = req.target_agent_url 或 _default_target_agent_url()(AGENT_BASE_URL 或 http://127.0.0.1:10123)
       url = f"{base_url}{req.endpoint}"
       httpx.AsyncClient(timeout=httpx.Timeout(req.timeout_s))  # 单一总超时
       POST url, json=payload, headers={"x-run-id": run_id}?
       返回 {target_url, status_code, headers(全量原始), body(json 或 raw_text)}
  -> FastAPI JSONResponse

实际 Agent 侧：POST /run (main.http_run)
  -> normalize_request_payload + _validate_normalized_payload
  -> resolve_profile_id(source_channel, requested_profile, headers)  # customer_support | customer_ceshi
  -> _resolve_request_llm_route(payload)
  -> asyncio.create_task(service.run(payload, ctx)); service.running_tasks[run_id]=task
  -> asyncio.wait_for(task, timeout=TIMEOUT_SECONDS)
  -> result["run_id"]=run_id; _sanitize_customer_support_run_result(result, profile)
  -> response_mode = payload.get("response_mode","full"); compact 时 _compact_run_response(...)
  -> _log_api_call_event(route="/run", status, http_status_code, latency_ms, ...)
  -> 返回 result（compact/full）
```

关键点：
- `/run` 是真实同步 HTTP 调用，经过 `service.run` -> LangGraph `graph.invoke`。
- admin 代理 `proxy_test_run` 只做 HTTP 转发，**不注入任何调试 Trace**；返回全量原始 headers（含潜在敏感头）。
- `target_agent_url` 可被请求任意指定 -> **SSRF 风险**。
- 超时为单一 `httpx.Timeout(req.timeout_s)`，无 connect/read/write 分级。
- 未返回 latency/run_id 维度（仅透传 body）。

## 2. 当前 `/stream_run` 调用链

```
浏览器 ChatDebugPage.streamTestRun -> POST /admin/test/run {endpoint="/stream_run", stream=true}
  -> admin_router.admin_test_run: is_streaming=True
  -> service.stream_test_run(req)
       base_url = req.target_agent_url 或默认；url=f"{base_url}/stream_run"
       httpx.AsyncClient(timeout=httpx.Timeout(req.timeout_s))
       client.send(build_request(POST, url, json=payload, headers={"x-run-id":...}), stream=True)
       # 不检查 upstream status_code 即开始流式透传
       _iterator(): async for chunk in response.aiter_raw(): yield chunk
                    finally: response.aclose(); client.aclose()
  -> StreamingResponse(iterator, media_type=upstream content-type, status_code=upstream status)

实际 Agent 侧：POST /stream_run (main.http_stream_run)
  -> normalize + validate + resolve_profile_id + llm_route
  -> agent_stream_handler(payload, ctx, run_id,
        stream_sse_func=service.explainable_stream_sse,
        sse_event_func=service._sse_event,
        register_task_func=_register_task, ...)
  -> StreamingResponse(stream_generator, media_type="text/event-stream")
       分支：
        - profile=="customer_support": service.explainable_stream_sse
            graph.astream(stream_mode=["updates"]) -> build_customer_support_debug_events_from_update(update, cursor)
            -> 每个 event 经 self._sse_event(event) 输出 "event: message\ndata: {json}\n\n"
        - 其他（customer_ceshi）: service.stream_sse
            self.astream -> stream_runner.astream -> graph.astream 节点级 update
            -> self._sse_event(chunk)
  -> _log_api_call_event(route="/stream_run", status="streaming")
```

关键点：
- `/stream_run` 是真实 HTTP+SSE。
- admin 流式代理 `stream_test_run` **未检查 upstream 状态码**，upstream 4xx/5xx 会被当作 SSE 正文透传。
- **无客户端断开检测**：浏览器断开不会主动关闭 upstream；`finally` 只在迭代结束/异常时关闭。
- **停止按钮只 Abort 浏览器 fetch**，未调用 `/cancel/{run_id}` 取消上游运行（见 §8）。
- 无 heartbeat / 空闲检测。
- 单一总超时，无分级超时。

## 3. customer_support 的事件来源

走 `service.explainable_stream_sse`（main.py:581-625）：
- 真实来源：LangGraph `graph.astream(stream_mode=["updates"])` 的节点 update（`route`/`delegate`/`check`/`finalize`/`fail`）。
- 这些 update 经 `customer_support_stream_debug.build_customer_support_debug_events_from_update` 转换为事件：
  - `message_start`：`route`/`delegate` 节点首次出现时生成。
  - `thinking`：由 `_events_from_delegate_state`/`_events_from_check_state` 用**模板句**拼装（"1. 前置安全与标准 Agent 装配。"、"2. 后置内容质检。" 等），并非模型真实推理。
  - `tool_response`：从 `state.route_trace.tool_call_sequence` 推导，文案"已执行工具：{name}"，`result={"status":"completed"}`，**无 tool_request/started 配对，无 call_id，且在未核实真实结果时即标记 completed**。
  - `answer`：`_events_from_terminal_state` 从 `messages` 末尾 AIMessage 提取真实最终回答（真实）。
  - `message_end`：终态生成。
- 另存在未被 live 路径调用的 `build_customer_support_debug_events(payload)`（静态版），含更严重的伪造（见 §7）。

## 4. customer_ceshi 的事件来源

走 `service.stream_sse` -> `astream` -> `stream_runner.astream` -> `customer_ceshi_responses` 图：
- 该图为单节点图：`START -> customer_ceshi_responses(_run_native_loop) -> END`。
- `_run_native_loop` 同步调用 `runtime.invoke`，内部是**同步** `responses_client.responses.create(...)` 的工具循环（builder.py:821/961/1364/1411/1428/1471/1486/1983 均为同步 `.create`）。
- 因此 `graph.astream` 只能在节点完成时产出**一个最终 update**，**没有 Responses 原生 token 流**。
- capability 探测（`probe_capabilities`）只探测 LangChain `client.stream()`（Chat 风格流），非 Responses 流；注释明确"LangChain ChatOpenAI does not expose a provider-independent Responses API"。
- 结论：customer_ceshi 当前**不支持原生 Responses token 流**，stream 仅交付节点级最终结果。

## 5. 前端当前如何解析 SSE

`frontend/src/api/client.ts: consumeEventStream`：
- 用 `fetch` + `ReadableStream`，`TextDecoder("utf-8",{stream:true})` 累积 buffer。
- **仅按 `\n\n` 分块**，不处理 `\r\n\r\n`（CRLF 流会聚成单块无法解析）。
- 解析 `event:` 与多行 `data:`；**忽略 `id:` 行**；**忽略注释 heartbeat（`:` 开头）**。
- 尾部未闭合 buffer 直接当一块 emit，不区分 incomplete。
- 无 `run.completed` 终止判定；流结束即 `onDone`，不标记 `incomplete_stream`。
- 无重复 event id 去重。
- 非 200 抛错；Abort 透传 signal。
- UTF-8 跨 chunk：`TextDecoder({stream:true})` 正确处理（OK）。

事件渲染（`ChatDebugPage.tsx`）：
- `resolveEventType`：event 名非 "message" 则直接用，否则读 `data.type`，否则 `raw`。
- `getEventText`：遍历 `text/delta/output_text/answer/content.answer/content.text/content.content` 任一字符串即当正文 -> **"任意包都当回答"反模式**；`raw` 事件若有文本也并入 answer。
- 事件类型集合：`message_start|thinking|tool_request|tool_response|answer|message_end|upload|raw`。
- `thinking` 标签为"思考过程"。
- 顶部为四顶层 Tab（对话/Trace/API/原始日志）；header 固定显示"Agent：Hifleet 主 Agent"。
- Profile 在"高级参数"表单，非顶层核心控件；**无 /run 与 /stream_run 切换**（`runTest` 已导入但未调用，仅 `streamTestRun`）。

## 6. 哪些事件是真实运行事件

- `/run` 返回的 `result`（含真实 `run_id`、最终回答、`llm_route`）。
- customer_support 的 `answer`（来自真实 AIMessage）、`message_start`/`message_end`（来自真实节点到达）。
- customer_support `check_result`（来自真实 Check 节点状态）。
- customer_ceshi 节点级最终 update（真实最终结果，但非 token 流）。
- 观测日志 `_log_api_call_event`（route/status/latency/http_status_code 真实）。

## 7. 哪些内容是静态生成、推测或事后拼装

- **文件名特判（必须删除）**：`_attachment_hint` 依附件名 `01_query`/`全球海图` 推断"安全水域浮标"；依 `03_query`/`圈圈` 推断"锚地或锚泊区域范围圈"。
- **预伪造工具调用/结果（必须删除）**：`build_customer_support_debug_events` 静态版对未执行的检索生成 `tool_request`（`tool_name=smart_search`）与 `tool_response`（`result={"status":"planned"}`）。
- **模板句伪装模型思考（必须改造）**：`_events_from_delegate_state`/`_events_from_check_state` 的 `thinking` 事件为确定性模板文案，无 Provider 推理来源。
- **未配对/未核实的工具完成（必须改造）**：`tool_response` 仅来自 `route_trace.tool_call_sequence`，无 started 配对、无 call_id、未核实真实结果即 `completed`。
- **金标测试（必须删除/重写）**：`tests/test_customer_support_stream_debug.py` 的 `test_reference_01..04` 把上述伪造内容（"安全水域浮标"/"锚地"/"tool_request>=3"）作为固定断言。
- **前端"任意包当回答"（必须改造）**：`getEventText` 任意字段取值。
- **admin 代理全量透传 headers（必须脱敏）**。
- customer_ceshi 不存在 token 流却被前端 `raw` 兜底当作增量回答的风险。

## 8. 最小改造计划

阶段 A（协议与夹具，不动正式页面）：
1. 新建 `src/debug_events/`：`contracts.py`(DebugEvent V1 schema/类型/seq)、`redaction.py`(脱敏+大小限制+签名URL参数脱敏)、`chat_adapter.py`、`responses_adapter.py`、`langgraph_adapter.py`、`emitter.py`(seq 单调/run_id/call_id 配对)、`aggregator.py`。
2. 删除 stream_debug 中的文件名特判、预伪造 tool_request/planned tool_response、模板句 thinking；改为 `runtime_summary` 来源标记的真实状态摘要；工具事件改为真实 started/completed 配对+call_id。
3. 删除/重写 `test_reference_01..04` 金标测试。
4. 后端单测：schema/seq 单调/run_id/call_id 配对/脱敏/未知事件降级。

阶段 B（后端本地验证）：
5. admin 代理修复：SSRF allowlist（仅允许配置的 Agent 地址）、分级超时(connect/read/write/pool)、upstream 状态码检查、客户端断开关闭 upstream、`/cancel/{run_id}` 接入、heartbeat、headers 脱敏、`/run` 返回 latency/run_id。
6. Agent `/stream_run` 在受保护内部调试 Header（服务端密钥，仅 `/admin/test/run` 注入）下输出 DebugEvent V1；普通流保持客户安全输出。
7. customer_ceshi 步骤流：在同步工具循环前后发真实 `tool.started`/`tool.completed`，最终一次 `answer.completed`，标记"步骤流/Provider 未提供 Token 增量"；Chat fallback 标记 `requested_runtime/effective_runtime/fallback_reason`。
8. 四象限本地 E2E（curl /run 与 /stream_run × 两 profile）。

阶段 C（本地 harness）：最小调试页证明事件协议。

阶段 D（正式页面）：
9. 前端独立 SSE parser（`\n\n`+`\r\n\r\n`、多行 data、`id:`、注释 heartbeat、UTF-8 跨 chunk、重复 id 去重、incomplete_stream、Abort）。
10. ChatDebugPage：顶层 /run|/stream_run 切换、profile 切换+运行时说明、执行过程/工具卡片/证据Guard、指标、三栏布局；仅按 DebugEvent V1 `type` 渲染；移除"任意包当回答"。
11. 敏感数据：签名URL不持久化、URL 查询参数脱敏、Authorization/Token 显示 `***`。
12. Session 契约：显式保存/恢复 agent_profile/endpoint/response_mode/requested/effective runtime；Session key 关联 environment+profile+user+session。
13. 文档：ADMIN_CHAT_DEBUG、DEBUG_EVENT_PROTOCOL、CUSTOMER_SERVICE_API、E2E 报告、映射表。
14. 回归：customer_support/customer_ceshi/Shared Skills V2/后台 API/前端 TS+prod build。
