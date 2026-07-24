# 对话测试工作台本地 E2E 验证报告

> 分支 `codex/shared-skills-v2`。本报告记录四象限验证方法与已离线验证项，以及需真实 LLM/DB 服务的限制。

## 1. 四象限

| 接口 \ Profile | customer_support | customer_ceshi |
| --- | --- | --- |
| `/run` | 真实非流式 HTTP | 真实非流式 HTTP |
| `/stream_run` | 真实 SSE + LangGraph V1 流 | 真实 SSE + 步骤流 |

每个象限应覆盖：普通无需工具、本地知识库、Web Search、船舶查询、多轮上下文、工具失败、模型失败/fallback、超时、取消、中文 SSE 跨 chunk、附件输入、证据不足/Guard。

## 2. 离线已验证（自动化测试）

以下均通过：

### 后端（pytest，`PYTHONPATH=src` 已配 `pyproject.toml`）
- DebugEvent Schema / seq 单调 / run_id 关联：`tests/test_debug_events.py`（17）
- Chat adapter / Responses adapter / Responses fallback / 未知事件降级 / 工具配对 / 脱敏 / 附件 URL 脱敏 / incomplete 检测 / 终态守卫：`tests/test_debug_events.py`
- V1 流（两 profile，FakeGraph）：`tests/test_debug_stream_v1.py`（4）
- 伪 Trace/文件名特判/预伪造工具已删除：`tests/test_customer_support_stream_debug.py`（7，已删除 4 个金标测试）
- SSRF allowlist / 分级超时 / /run latency+run_id+脱敏 headers / 流 upstream 状态 / 客户端断开 / 取消代理 / heartbeat：`tests/test_admin_proxy.py`（12）
- Session 契约（agent_profile/endpoint/response_mode 持久化与恢复）：`tests/test_chat_debug_session_contract.py`（3）
- 原链路回归（customer_ceshi / skills_v2 / trace_redaction / import boundaries）：121 passed
- 管理台/主运行时辅助：`test_main_runtime_helpers.py`、`test_admin_upload_config.py`：8 passed

### 前端（vitest）
- SSE `\n\n` / `\r\n\r\n` / 多行 data / `id:` / 注释 heartbeat / 尾部未闭合 / UTF-8 跨 chunk / duplicate id / run.completed 终止 / incomplete / Abort / 终态集合：`frontend/src/api/__tests__/sseParser.test.ts`（15）
- V1 类型识别 / answer.delta 拼接 / tool started/completed 配对（不重复计数）/ 终态 / raw_provider_event 不当答案 / 敏感字段脱敏 / 签名 URL 脱敏：`frontend/src/api/__tests__/debugEvent.test.ts`（10）

### 构建
- 前端 `tsc -b && vite build`：通过。

## 3. 需真实服务的验证项（限制）

下列需真实 ARK_API_KEY/Postgres/LLM 在线，本环境部分测试（如 `test_customer_support_router`、`test_customer_support_intent_agent`、`test_customer_ceshi_readable_trace` 等）会发起真实模型调用，不适合在 CI 离线跑。建议在具备密钥的环境执行真实四象限 curl：

```bash
# 启动服务
uvicorn src.main:app --host 127.0.0.1 --port 10123 &

# /run customer_support
curl -sS -X POST http://127.0.0.1:10123/run \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"你好"}],"session_id":"e2e:cs:run:1","agent_profile":"customer_support","response_mode":"compact"}'

# /stream_run customer_ceshi（含内部调试 Token）
curl -N -X POST http://127.0.0.1:10123/stream_run \
  -H 'content-type: application/json' \
  -H "x-internal-debug-trace: $INTERNAL_DEBUG_TRACE_TOKEN" \
  -d '{"messages":[{"role":"user","content":"航线看不到怎么办"}],"session_id":"e2e:ceshi:stream:1","agent_profile":"customer_ceshi"}'

# 取消
curl -X POST http://127.0.0.1:10123/cancel/<run_id>
```

每象限确认：最终回答存在、无重复回答、无重复工具、工具 request/response 配对、run.completed 正确、无伪造事件、无敏感字段泄露、`/run` 与 `/stream_run` 最终客户回复语义一致、不同 Session 不串上下文。

## 4. 已知不相关失败

`tests/test_smart_search_tools.py::test_web_search_passes_and_enforces_block_hosts` 在干净 HEAD `bfd29d0`（无本次改动）即失败，属 web_search block_hosts 过滤的既有 bug，与本次工作无关，不在本次修复范围。
