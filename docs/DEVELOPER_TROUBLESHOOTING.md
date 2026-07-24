# HiFleet Agent 开发反馈与排查手册

本手册面向收到用户反馈、回归失败或线上观测异常的开发人员。目标是先确定**链路、事实和安全边界**，再修改代码；不要直接根据自然语言猜测问题原因。

## 1. 五分钟定位流程

1. 确认发生环境、时间、`agent_profile`、`source_channel`、脱敏 `session_id`/`run_id`。
2. 保存客户可见输入和回复；媒体仅保存受控文件名或内部引用，不保存签名 URL。
3. 查看 `metrics` 与 `route_trace`：重点关注 `runtime_mode`、`skills_runtime`、`tool_names`、`guard_result`、`update_draft_status`、`fallback_reason`。
4. 用相同 Profile 和稳定 `session_id` 通过 `/run` 最小复现；写入类问题只做 Draft 或测试环境 dry-run。
5. 按下表选择代码入口、测试和文档；修复后先跑最小测试，再跑对应保护回归。

## 2. 问题 → 入口 → 验证

| 现象 | 先检查 | 主要代码/配置 | 最小验证 |
| --- | --- | --- | --- |
| 请求 4xx/5xx、SSE 格式异常、Profile 选错 | 请求字段、`agent_profile`、日志 | `src/main.py`、`config/agent_profiles.json` | `tests/skills_v2/test_http_contract.py` 与 `/run` 最小请求 |
| 正式客服回复/路由回归 | `customer_support` trace、Guard、工具序列 | `src/agents/agent.py`、`src/agents/customer_support_router.py` | 对应 `tests/test_customer_support_*.py` |
| `customer_ceshi` 工具或 Prompt 异常 | `skills_runtime.mode`、`source_versions`、工具 schema | `src/agents/customer_ceshi_responses/builder.py`、`src/skills/adapters/customer_ceshi.py` | `tests/skills_v2/`、`tests/customer_ceshi_v2/test_responses_runtime.py` |
| V2 加载失败后工具变多 | `skills_runtime.fallback_reason`、工具列表 | `src/agents/customer_ceshi_responses/builder.py`、`src/agents/customer_ceshi_v2/tools.py` | `tests/skills_v2/test_customer_ceshi_v2_registry.py` |
| 平台规则、会员权限、格式结论不可靠 | `guard_result`、知识库证据、检索次数 | `claim_guard.py`、`knowledge_retrieval`、场景分类 | `tests/customer_ceshi/test_claim_guard.py`；检查原始证据而非工具成功状态 |
| 搜索重复、Browser 越权或超时 | `tool_names`、搜索预算、URL 来源 | `NativeToolRuntime`、`CapabilityRegistry` | `tests/customer_ceshi_v2/test_responses_runtime.py` |
| 船位/静态信息“更新成功”争议 | Draft 状态、真实下游状态、确认会话 | `ship_info_update`、`ship_updates.py` | `tests/customer_ceshi/test_ship_updates.py`、`test_write_confirmation.py` |
| 上游 HiFleet 数据能力/版本异常 | `skills-lock.json`、`upstream_commit`、同步输出 | `scripts/sync_hifleet_skills.py`、`src/skills/hifleet_data/` | `tests/skills_v2/test_hifleet_sync.py` |
| customer_support V2 影子差异 | `skills_v2_shadow`、`prompt_injected`、`write_state` | `src/skills/adapters/customer_support.py`、`src/agents/agent.py` | `tests/skills_v2/test_customer_support_shadow_graph.py` |

## 3. 安全边界速查

- `customer_support` 默认是 legacy；不要因排障把主链改成 V2 或复用 `customer_ceshi` Builder。
- V2 模型层只能使用 `prepare_ship_update`、`commit_ship_update`、`cancel_ship_update`；绝不能暴露 `upload_ship_position` 或 `update_ship_static_info`。
- `web_search` 是唯一搜索入口；`verify_public_page` 只能验证用户提供或当轮 `web_search` 返回的 URL。
- 任何 V2 失败回退不得恢复 `agent_browser_deep_search`、`web_search_agent_browser`、知识库管理或写工具。
- 工具成功不等于语义正确；平台功能、权限、格式、海图符号等高风险结论必须有可追溯证据。
- `Draft`、`dry-run`、请求受理都不能写成“更新成功”；只有真实接口 `success` 才能这样回复。

## 4. 常用验证命令

```bash
# Shared Skills V2、customer_ceshi 和写入保护
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/skills_v2 \
  tests/customer_ceshi_v2/test_tool_registry.py \
  tests/customer_ceshi/test_ship_updates.py \
  tests/customer_ceshi/test_claim_guard.py \
  tests/customer_ceshi_v2/test_write_confirmation.py \
  tests/customer_ceshi_v2/test_responses_runtime.py

# customer_support 保护回归
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/test_customer_support_dialog_analysis.py \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_p0_optimization.py \
  tests/test_customer_support_router.py \
  tests/test_customer_support_stream_debug.py \
  tests/test_wechat_customer_support_http.py

# 上游候选版本仅审计，不改 lock
PYTHONPATH=src:. .venv/bin/python scripts/sync_hifleet_skills.py --revision HEAD
```

外部 HTTP、媒体和真实模型验证必须在非生产环境完成。具体请求格式见 [CUSTOMER_SERVICE_API.md](CUSTOMER_SERVICE_API.md)，Shared Skills V2 的结果说明见 [shared_skills_v2/TESTING.md](shared_skills_v2/TESTING.md)。

## 5. 反馈记录模板

```text
问题标题：
发生时间与环境：
Profile / source_channel：
脱敏 session_id / run_id：
用户输入摘要与附件类型：
客户可见回复：
期望结果：
metrics 摘要（runtime_mode、skills_runtime、tool_names、guard_result、Draft 状态）：
route_trace / 日志摘要：
复现请求与最小测试：
是否涉及写入、权限、平台规则或敏感数据：
```

将修复对应到一个可重复的测试或回归 fixture；若不能复现，记录证据缺口，不要把猜测写入产品规则或验收结论。
