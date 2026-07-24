# DebugEvent V1 协议

对话测试工作台的统一事件协议。前端 **只按 `type` 渲染**，不再直接解析 Chat Completions 与 Responses API 的原始包。所有事件来自真实运行状态、真实回调或 Provider 明确提供的数据，绝不伪造隐藏思维链或工具执行。

## 1. 协议定位

- `/run`：非流式，返回最终结果（compact/full）。**不产生流式 Trace**。
- `/stream_run`：SSE 流式。普通客户流保持安全输出；管理台通过 **服务端内部调试 Token**（仅 `/admin/test/run` 代理注入）开启 DebugEvent V1 流。
- 外部调用方传同名 Header `x-internal-debug-trace` **不会** 开启调试（Agent 校验 Token，浏览器前端不持有该 Token）。

## 2. 事件结构

```json
{
  "schema_version": "debug-event.v1",
  "event_id": "evt_xxx",
  "seq": 12,
  "timestamp": "2026-07-24T08:30:00.123Z",
  "run_id": "run_xxx",
  "session_id": "session_xxx",
  "agent_profile": "customer_ceshi",
  "endpoint": "/stream_run",
  "runtime": "responses",
  "provider": "volcengine_ark",
  "model": "deepseek-v4-flash-260425",
  "type": "tool.completed",
  "phase": "evidence_retrieval",
  "summary": "知识库检索完成，返回 3 条候选证据。",
  "call_id": "call_xxx",
  "parent_event_id": "evt_xxx",
  "duration_ms": 842,
  "data": {},
  "visibility": "admin_safe",
  "source": "runtime_summary"
}
```

不变量（由 `DebugEmitter` 与 `DebugAggregator` 强制）：
- `seq` 在单个 run 内严格单调递增；
- 每个事件带 `run_id`；
- 工具事件带稳定 `call_id`；`tool.completed`/`tool.failed` 的 `parent_event_id` 指向对应 `tool.started`；
- `answer.delta` 只含增量文本；`answer.completed` 含完整答案或其 hash/length；
- 终态事件（`run.completed`/`run.cancelled`/`run.timeout`/`run.failed`）后只允许 `heartbeat`；
- 缺少终态事件时标记 `incomplete_stream`；
- 未知 Provider 事件只能进入 `raw_provider_event`，不得被识别为答案。

## 3. 事件类型

| type | phase | 说明 | 来源要求 |
| --- | --- | --- | --- |
| run.started | intake | 运行开始 | 真实 |
| input.normalized | intake | 输入标准化 | 真实 |
| route.selected | routing | 路由/运行时选择（含 fallback） | 真实 |
| phase.started / phase.completed | * | 阶段开始/结束 | 真实 |
| reasoning.summary | understanding | 推理摘要 | `source=provider` 仅当 Provider 明确返回；否则 `source=runtime_summary` |
| tool.started | tool_execution | 工具真实开始执行 | 真实回调 |
| tool.arguments.delta | tool_execution | 工具参数增量 | 真实 |
| tool.completed / tool.failed | tool_execution | 工具真实返回/失败 | 真实结果 |
| evidence.summary | evidence_retrieval | 证据是否充足 | 真实观察 |
| guard.result | guard | 安全检查/兜底 | 真实 Check 状态 |
| answer.started / answer.delta / answer.completed | response_synthesis | 最终回答 | 真实 Provider 增量或真实最终文本 |
| run.completed / run.cancelled / run.timeout / run.failed | finalization | 运行终态 | 真实 |
| heartbeat | - | 空闲心跳 | 运行时 |
| raw_provider_event | - | 未识别 Provider 事件（仅调试区） | 原始 |

## 4. 页面用语（禁止误导）

统一使用：**执行过程 / 推理摘要 / 工具调用 / 证据与结果 / 输出整理**。
禁止：完整思维链、模型脑内过程、思考过程（已重命名为“推理摘要”）。

## 5. 什么是“执行摘要”

由确定性运行时状态（路由、节点到达、Check 结果、工具观察）生成的简短摘要，`source=runtime_summary`。它 **不是** 模型原始思维。当 Provider 未返回 reasoning summary 时，只展示这种摘要，且明确标记来源。

## 6. 为什么不展示完整隐藏思维链

系统 Prompt 全文、隐藏推理 Token、Provider 原始内部思维、API Key/Authorization/Cookie、签名 URL、数据库连接信息均被脱敏或不持久化（见 `redaction.py` / `debugEvent.ts`）。`reasoning.summary` 仅在 Provider 明确返回时标 `source=provider`，否则为 `runtime_summary`。

## 7. 模块结构

```
src/debug_events/
  contracts.py       # DebugEvent schema、类型、phase、runtime、visibility
  redaction.py       # 脱敏、签名 URL 参数脱敏、headers 脱敏、大小限制
  emitter.py         # seq 单调、run_id、call_id 配对、终态守卫、metrics
  chat_adapter.py    # Chat Completions chunk -> V1（content/reasoning/tool_calls）
  responses_adapter.py # Responses 原生事件 + 同步步骤模式 + fallback
  langgraph_adapter.py # customer_support 节点 update -> V1（真实状态，无伪造）+ ToolCallCallbackHandler
  aggregator.py      # 校验配对/seq/run_id/duplicate/incomplete
src/agents/debug_stream_v1.py # V1 SSE 流（两 profile），受内部 Token 门控
frontend/src/api/sseParser.ts # 鲁棒 SSE 解析器
frontend/src/api/debugEvent.ts # 前端 V1 类型识别/配对/脱敏
```

## 8. Chat / Responses 原始事件 -> DebugEvent 映射

### Chat Completions（customer_support）

| 原始 | DebugEvent V1 |
| --- | --- |
| graph.astream node update `route` | route.selected |
| node update `delegate`（route/task_type/attachments/route_trace） | reasoning.summary(runtime_summary)、evidence.summary |
| node update `check`（check_result） | guard.result |
| node update `finalize`/`fail`（AIMessage） | answer.started、answer.completed |
| LangChain `on_tool_start` 回调 | tool.started（call_id=run_id） |
| `on_tool_end` / `on_tool_error` | tool.completed / tool.failed |
| Chat delta.content | answer.delta |
| Chat delta.reasoning_content | reasoning.summary(source=provider) |
| Chat delta.tool_calls（id 首现） | tool.started |
| Chat delta.tool_calls（arguments） | tool.arguments.delta |
| 未知 chunk | raw_provider_event |

### Responses API（customer_ceshi）

当前 Provider **不支持原生 Responses token 流**（同步 `responses.create()` 工具循环），因此使用 **步骤流**：

| 真实来源 | DebugEvent V1 |
| --- | --- |
| 节点 update（同步循环完成后的 observations） | 对每条真实 observation：tool.started + tool.completed/tool.failed |
| 节点 update generated_answer | answer.started + answer.completed（一次性，非假 token 流） |
| requested_runtime_mode != effective_runtime | route.selected（标记 fallback：requested/effective/fallback_reason） |
| 节点 update status=success/degraded/failed | run.completed / run.failed |
| metrics | run.completed.data.provider_metrics |

若未来 Provider 支持原生 Responses 流，`adapt_responses_event` 已映射：`response.created`→run.started、`response.output_text.delta`→answer.delta、`response.output_item.added`(function_call)→tool.started、`response.function_call_arguments.delta`→tool.arguments.delta、`response.output_item.done`→tool.completed、`response.completed`→answer.completed+run.completed、`response.failed`→run.failed、其余→raw_provider_event。

## 9. 如何判断 Responses 是否真正流式

运行 capability probe（`probe_capabilities`）。当前结论：**未验证原生 Responses token 流**。customer_ceshi V1 流标记为“步骤流：Provider 未提供 Token 增量”，且不把同步结果拆成假 token 流。Chat fallback 时 `route.selected` 明确显示 `effective_runtime=chat_function_calling`，不显示 Responses 已成功。
