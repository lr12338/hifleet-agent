# customer_ceshi Responses 重构交付状态

日期：2026-07-15

## 运行设计

- **主 Orchestrator**：`deepseek-v4-flash-260425`，通过原生 Responses function call 或 Chat Function Calling 持续决定下一步、读取 Observation、决定完成。
- **多模态**：`doubao-seed-2-0-lite-260428` 只在原生 `inspect_media` 工具被主模型调用时运行；不充当第二个决策 Agent。
- **Responses 主路径**：`OpenAI.responses.create` 发送 `tools`，解析 `function_call`，将应用执行结果作为 `function_call_output` 回传，并按 session 续传 `previous_response_id`。
- **Chat 降级路径**：`ChatOpenAI.bind_tools` 返回的原生 `tool_calls` 使用 `ToolMessage(tool_call_id=...)` 回填。没有使用 `AgentDecision`。
- **隔离**：运行时 checkpoint 标识固定为 `customer_ceshi_responses:{thread}` / `customer_ceshi_responses`，不会导入或回退到 `customer_support`。
- **安全**：写工具、文件写入、下载上传和 Python 执行工具仍在 allowlist 前拒绝；高风险无 Observation 的最终答案会被阻断；本地媒体业务身份推断被拒绝。

## 已替换 / 保留

- 新增 `agents.customer_ceshi_responses`，作为原生工具调用运行时。
- `customer_ceshi_v2` 保留为 feature-flag 的短期 `legacy_v2` 回滚目标，未再作为默认原生工具选择路线。
- `customer_support` builder、state、prompt、checkpoint 和返回格式未修改。

## Feature Flag

`config/agent_llm_config.json`：

```json
"customer_ceshi_runtime": {
  "mode": "responses",
  "fallback_mode": "chat_function_calling",
  "responses_enabled": true,
  "chat_fallback_enabled": true,
  "legacy_v2_enabled": false
}
```

将 `mode` 改为 `chat_function_calling` 可跳过 Responses；临时调试旧实现时必须同时设置 `mode: legacy_v2` 与 `legacy_v2_enabled: true`。任何失败都不会回退到 `customer_support`。

## 验证结果

| 类别 | 命令 / 范围 | 状态 | 结果 |
|---|---|---|---|
| Mock：Responses 两轮工具调用 | `PYTHONPATH=src uv run pytest -q tests/customer_ceshi_v2/test_responses_runtime.py` | PASSED | 连续两次 `function_call`，`resp-1 → resp-2` 的 `previous_response_id` 和 `function_call_output` 均被验证。 |
| Mock：Chat Function Calling 降级 | 同上 | PASSED | Responses 抛出不支持错误后，主模型以 `bind_tools` / `ToolMessage` 完成工具循环。 |
| Import Boundary / Builder | `tests/customer_ceshi_v2/test_entry_isolation.py`、`test_responses_import_boundaries.py` | PASSED | `customer_support` 未构建新运行时；新运行时不导入生产 customer support 模块。 |
| customer_support 回归 | `PYTHONPATH=src uv run pytest -q tests/test_customer_support_router.py ...` | PASSED | 130 passed；包含 customer support router 与运行时隔离用例。 |
| customer_ceshi_v2 回归 | `PYTHONPATH=src uv run pytest -q tests/customer_ceshi_v2 ...` | PASSED | 现有 v2 suite 通过；真实模型 smoke 为 SKIPPED。 |
| 混合旧 customer_ceshi 写状态 | `tests/test_customer_ceshi_pending_state.py::test_ambiguous_update_creates_awaiting_operation_pending` | FAILED | 基线旧生产轻量链测试期望 pending write state，但当前仓库在未修改其代码的情况下返回 inactive；与新 Responses runtime 没有调用关系。 |
| HTTP `/run` 文本 | 本地服务 + 真实模型 | NOT_RUN | 无服务凭据 / endpoint。 |
| HTTP `/run` 图片 | 本地服务 + 真实模型 | NOT_RUN | 无服务凭据 / endpoint。 |
| `test/image` fixtures | 真实 Doubao 感知 | SKIPPED | 没有真实模型凭据；未把本地规则结果记为通过。 |
| Async checkpoint、`ainvoke`、`astream` | 真实 HTTP | NOT_RUN | facade 已提供这些接口；尚未用部署端点验证。 |
| Responses SSE token 流 | 真实 endpoint | NOT_IMPLEMENTED | 当前 facade 在最终图更新时输出；需要有凭据 PoC 后接入 provider SSE 事件转发。 |

## 实际环境探测

- `COZE_WORKLOAD_IDENTITY_API_KEY`、`COZE_INTEGRATION_MODEL_BASE_URL`：未设置（仅检查变量是否存在，未输出任何值）。
- 因此：真实 Responses、真实 Chat 降级、图片模型和 HTTP 服务均为 **SKIPPED / NOT_RUN**，不是 PASSED。

## 已知限制与下一步

1. 在隔离环境配置实际方舟 endpoint 与凭据，运行 Responses 双工具、Chat 降级、图片 fixture、`/run`、`/stream_run`、多 session 和 `ainvoke` / `astream` 集成验证。
2. 根据真实回包补齐 Context Editing、Caching、续写和 SSE 事件字段，不能从 OpenAI Responses 规范反推火山方舟的网关兼容性。
3. Managed Agents 仅建议后续 PoC：比较其 Tools、MCP、Skills、Session、Memory 和权限策略，不能直接替换本次安全边界。
