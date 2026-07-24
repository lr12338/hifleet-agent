# Chat Debug 对话测试工作台使用说明

真实、准确、可复现的对话测试工作台。页面展示的每一个步骤都有真实运行事件、状态或 Provider 数据支撑。

## 1. 顶部核心控制

- **接口模式** `/run`（非流式）/ `/stream_run`（SSE）；
- **Agent Profile** `customer_support`（正式 Chat 客服链，LangGraph）/ `customer_ceshi`（Responses 优先实验链，不支持原生 Token 流，步骤流）；
- **模型** 自动路由或指定；
- **推理模式** 开启/关闭；
- **响应模式** `compact`/`full`（仅 `/run` 显示）；
- 发送、停止、重新运行。

切换 Profile 显示运行时说明（不再显示“HiFleet 主 Agent”）。

## 2. /run 模式

- 真实非流式 HTTP 调用（经 `/admin/test/run` 代理转发到 Agent `/run`）；
- 发送后等待，完成后一次性展示客户回复；
- 显示 HTTP 状态、总延迟、run_id（右侧请求/响应）；
- 不显示伪流式动画、不显示流式 Trace。

## 3. /stream_run 模式

- 真实 HTTP+SSE，经服务端内部调试 Token 注入获取 DebugEvent V1 流；
- 回答区按 `answer.delta` 增量接收真实文本；
- 工具调用显示为卡片：开始时 loading，完成后显示耗时/状态/脱敏摘要；
- 推理摘要（`reasoning.summary`）按来源标记，`runtime_summary` 不可伪装为模型思维；
- Guard/证据不足使用独立状态卡；
- Provider 不支持 token stream 时标记“步骤流”。

## 4. 停止/取消

停止按钮先 Abort 浏览器 fetch，再调用 `/admin/test/cancel/{run_id}` 取消上游运行（不再只中断前端）。上游在 await 点优雅取消。

## 5. 执行详情

右侧详情（二级 Tab）：Overview / Execution / Tools / Evidence & Guard / Request / Response / Raw Events。对话视图、Trace、API、原始日志不再作为顶层 Tab 切走对话。

## 6. 指标

每轮展示 endpoint、profile、requested/effective runtime、model、run_id、session_id、HTTP 状态、总耗时、首事件、首 Token、模型调用次数、工具调用次数（一次完整调用计为 1，不把 request/response 分别计）、知识库/Web/媒体次数、fallback、guard、最终状态。

## 7. 会话持久化与恢复

- 会话保存到 Postgres（`chat_debug_sessions`），完整 session 对象存入 `payload` JSONB；
- 显式契约字段 `agent_profile`/`endpoint`/`response_mode` 一并写入 payload.meta 与 `_contract`；
- 刷新后恢复：Profile、endpoint、response_mode 正确恢复；
- 进行中 Session 刷新后不误判为 ended（仅终态事件置 ended）；
- 对比 `/run` 与 `/stream_run` 时使用两个独立但可关联的 Session，避免上下文污染（Session key 关联 environment+profile+user+session）。

## 8. 敏感数据保护

签名 URL 不写入持久化 Chat Session（仅保存 object key/文件名/MIME/大小）；Raw Request 对 URL 查询参数脱敏；Authorization/Cookie/API Key/Token 显示 `***`；工具返回内容设最大保存长度，超出保存摘要与 hash。

## 9. 常见故障排查

- **工具没有调用**：检查 `/stream_run` 是否有 `tool.started`；customer_ceshi 步骤流需等同步循环完成才有 observations。
- **工具调用失败**：`tool.failed` 卡片显示 error；检查观察 status。
- **fallback**：`route.selected` 显示 `effective_runtime=chat_function_calling` 与 `fallback_reason`，表示 Responses 不可用已回退。
- **流中断**：缺少 `run.completed` 时页面标记 `incomplete_stream`。
- **取消未生效**：确认停止按钮调用了 `/admin/test/cancel/{run_id}`（网络面板可见 POST）。
- **复现同一请求**：复制右侧 Request，用相同 session_id 重发。
- **接口对比**：用两个独立 session_id 分别发 `/run` 与 `/stream_run`，比较最终客户回复语义。
