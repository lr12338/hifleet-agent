# customer_ceshi Responses API 调研与能力矩阵

- 调研日期：2026-07-15
- 范围：仅火山引擎 / 火山方舟官方文档与当前项目实际 SDK 行为。
- 结论：`customer_ceshi` 采用 Responses 优先；当已配置网关不支持该端点或工具续传失败时，降级到原生 Chat API Function Calling。不会将旧 `AgentDecision` JSON 协议作为默认路径。

## 已阅读的官方页面

| 页面 | URL | 项目相关能力 | 关键请求 / 响应字段 | 工具与上下文续传 | 已知限制 / 处理 |
|---|---|---|---|---|---|
| 对话（Chat）API | https://www.volcengine.com/docs/82379/1494384 | Chat Completions、`messages`、流式 SSE、模型与推理参数 | 请求：`model`、`messages`、`stream`、`stream_options`；响应：assistant message、`tool_calls`（配合 Function Calling） | 工具结果以 `role: tool` 和对应 `tool_call_id` 回填，再发起下一次 Chat 请求 | 这是降级路径；不能假定其具有 Responses 的 response-id 上下文能力。 |
| 产品简介 | https://www.volcengine.com/docs/82379/1099455 | 方舟模型服务及访问前提 | 模型服务开通、推理接入点 / Model ID | 无直接工具循环字段 | 真实探测必须使用实际部署端点、实际模型和凭据。 |
| 工具概述 | https://www.volcengine.com/docs/82379/1827538 | Responses API 的内置工具、自定义函数、MCP；模型自行决定是否调用工具 | `tools`；自定义函数遵循 Function Calling 文档 | 自定义函数结果需要由应用执行并回传；可与 Responses 工具混合 | 本项目只暴露显式白名单的只读工具；写工具第一阶段保持禁用。 |
| 创建模型响应 | https://www.volcengine.com/docs/82379/1569618 | Responses API、`tools`、连续响应、思考等级、存储、流式 | 请求：`model`、`input`、`tools`、`previous_response_id`、`store`、`stream`、`max_output_tokens`、推理相关字段；响应：`id`、输出项、函数调用项、文本输出 | 对函数调用项执行后，以 `function_call_output` 及 `call_id` 回传；后续请求携带 `previous_response_id`。文档明确说明该字段会引入上一轮输入和回答并增加 token；连续轮之间建议约 100ms 延迟。 | 当前 OpenAI-compatible SDK 的 `ChatOpenAI` 表面不等价于 Responses；运行时使用独立 `OpenAI(...).responses.create` 适配，并以真实调用结果决定是否继续使用。 |
| Function Calling（函数调用） | https://www.volcengine.com/docs/82379/1262342 | Chat API 自定义函数调用 | `tools` / function schema、模型 `tool_calls`、工具结果 `tool_call_id` | 应用执行函数后把结果作为工具消息回填，模型可继续请求工具 | 仅作为 Responses 不可用时的原生降级；不再解析 `AgentDecision`。 |

## 相关官方能力映射

- **深度思考 / 工具调用**：Responses 创建请求支持推理工作量控制；运行时只记录配置等级和 token / 完成状态，不记录 reasoning content 或隐藏思维链。
- **多轮与上下文**：Responses 使用 `previous_response_id`；Chat 降级时保存原生 assistant tool calls 和 tool messages。当前实现为会话内缓存 provider response id，且在新会话不会跨 `customer_ceshi_responses:{tenant}:{user}:{session}` 命名空间复用。
- **流式**：两个官方 API 都支持 `stream`。当前 HTTP 图运行时可调用 `astream`，但真实 Responses SSE 和多轮工具交织尚未以有凭据端点验证，不能标为已通过。
- **多模态**：本项目不把 Doubao 作为第二个决策 Agent；只有模型调用 `inspect_media` 时才使用 Doubao 感知服务。工具输出区分成功 / 不确定 / 错误，不能按文件名或本地像素规则给业务身份结论。
- **结构化输出、上下文编辑 / 缓存、续写、Managed Agents**：保留为后续 PoC 调研项；本次 Agent 不迁移到 Managed Agents 运行时，避免改变现有 HTTP 与安全门禁。

## 当前 Capability Matrix

| 能力 | 文档支持 | 当前实现 | 当前环境真实探测 |
|---|---:|---:|---:|
| Responses 创建响应 | 是 | `OpenAI.responses.create` 适配器 | SKIPPED：未配置 `COZE_WORKLOAD_IDENTITY_API_KEY` / 推理 base URL |
| Responses 自定义函数 | 是 | 解析 `function_call`，回传 `function_call_output` | SKIPPED：同上；Mock 已通过两次连续工具调用 |
| `previous_response_id` 连续上下文 | 是 | 按隔离 session 保存并用于下一次工具续传 / 下一用户轮 | SKIPPED：同上；Mock 已验证 `resp-1 → resp-2` 续传 |
| Chat Function Calling | 是 | `ChatOpenAI.bind_tools` + `ToolMessage(tool_call_id=...)` | PASSED（Mock）；真实端点 SKIPPED |
| Chat 流式 | 是 | 图 facade 提供 `astream` | NOT_RUN：未以真实端点验证 token 流与工具交织 |
| Responses 流式 | 是 | 尚未实施 provider SSE 事件转发 | NOT_IMPLEMENTED |
| DeepSeek 主 Orchestrator | 配置前提 | 唯一模型调用循环与最终回答 | PASSED（单元测试） |
| Doubao 媒体工具 | 配置前提 | 仅 `inspect_media` 执行感知 | PASSED（架构）；真实图片模型 SKIPPED |
| 写工具默认关闭 | 项目安全策略 | `DENIED_TOOL_NAMES` 直接拒绝 | PASSED（代码路径） |

## 真实探测命令

真实调用只应在隔离测试环境并显式设置凭据后执行：

```bash
PYTHONPATH=src CUSTOMER_CESHI_REAL_MODEL_TEST=1 uv run pytest -q tests/customer_ceshi_v2/test_responses_runtime.py
```

本次环境没有上述凭据；未发送真实模型请求，也未把 SKIPPED 记为 PASSED。
