# Customer Support Agent Regression

本文描述当前 `customer_support` 需求理解主导链路的回归范围、测试入口、验收标准和线上排障重点。

## 1. 当前主链

```text
输入归一化 / 多模态感知
-> 需求理解 Agent 深度判断 intent/route/参数组/缺失项
-> 安全兜底与写操作保护
-> harness 或 planner 调用受控工具
-> 结果分析、回复修复、输出清洗
```

回归重点：

1. 需求理解 Agent 是否输出正确 `intent/route/task_type/tool_bundle`。
2. 图片、语音、视频当前轮 perception 是否进入需求理解。
3. 船位参数组 `position_update_params` 与静态参数组 `static_update_params` 是否完整记录。
4. 固定规则是否只做安全兜底，不再覆盖正常需求理解判断。
5. 船舶写操作是否必须通过 `execute_update_chain` 和工具硬校验。
6. 知识链是否仍按 `local_kb_search -> web_search -> web_search_agent_browser` 受控升级。
7. 最终输出是否经过 `sanitize_customer_output(...)`。

`_build_lightweight_customer_support_agent()` 仅保留为历史/回滚参考；当前 `build_agent(customer_support)` 应进入 `_build_customer_support_agent()`。

## 2. 测试入口

客服相关最小回归：

```bash
PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py
PYTHONPATH=src .venv/bin/python scripts/test_llm_config.py
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_router.py \
  tests/test_hifleet_ship_upload_position.py \
  tests/test_hifleet_ship_static_update.py \
  tests/test_smart_search_tools.py \
  tests/test_knowledge_admin_tools.py
```

语法级检查：

```bash
PYTHONPATH=src .venv/bin/python -m py_compile \
  src/agents/agent.py \
  src/agents/customer_support_router.py \
  src/agents/customer_support_guard.py \
  src/skills/hifleet_ship_service/tools.py
```

每次上线前应记录当前远端实际结果，不要沿用历史 passed 数。

## 3. 主链验收标准

一次成功客服请求通常应满足：

1. `phase_history` 包含 `route`、`execute`、`finalize`。
2. `route_trace.route` 是需求理解后的业务 route，如 `knowledge`、`ship_update`、`chart_symbol`。
3. `route_trace.reasoning_trace.intent_agent_result` 保留需求理解输出摘要。
4. 写操作场景可在 `route_trace.reasoning_trace.update_params` 看到规范化参数组。
5. `generated_tool_calls` 与真实执行工具一致。
6. 最终 `messages[-1].content` 已经过输出清洗。
7. 普通用户回复中不能出现 prompt、tool registry、reasoning trace、JSON、内部路径、key/token。

## 4. 重点回归场景

### 4.1 需求理解主导路由

- `帮我把目的港改成 RUPRI`：即使没有“静态信息”关键词，也应由需求理解识别为静态更新。
- `船型改散货船`：输出 `static_update_params.ship_type=散货船` 且 `minotype=散货船`。
- 图片/OCR 中含 `POSN`、时间、航速：输出 `position_update_params`，但缺身份时不更新。
- `船位更新慢/不刷新`：输出知识/排障意图，不进入写操作。

### 4.2 船位更新安全

- 有 MMSI + 经纬度 + 更新时间：允许调用 `upload_ship_position`。
- 缺 MMSI/IMO/船名：不调用上传工具，提示补充身份标识。
- 缺经度、纬度或更新时间：不调用上传工具，提示补充缺失项。
- 当前消息只有“更新一下”且历史有 MMSI：不复用历史 MMSI。
- 图片有经纬度和时间但无身份标识：不更新，只追问身份标识。
- 工具未明确成功：不得宣称已更新成功。

### 4.3 静态信息更新安全

- 当前输入有身份标识 + 静态字段：调用 `update_ship_static_info`。
- 只有船名：先搜索候选并要求确认 MMSI，不直接写入。
- `ship_type/minotype` 合法且一致：工具写入 `type` 和 `minotype` 同值。
- 船型目录外或 `ship_type/minotype` 冲突：只阻断船型字段，其他合法字段可继续更新。

### 4.4 知识与 Browser

- 本地 FAQ 强命中时优先用 `local_kb_search`。
- 平台操作类问题应覆盖入口、动作、保存/完成条件。
- 官方社区/帮助中心核验类问题应给具体公开链接，不只返回首页。
- 公共权威数据问题不能被错误限制到 HiFleet 站点。

### 4.5 多模态与微信旧接口

- 语音输入应先转写，再进入需求理解。
- 海图符号截图应走 `chart_symbol` 或多模态理解链。
- 微信旧 `/run` 的 `content.query.prompt` 应归一化为 `messages`。
- 回复必须可直接发给微信用户，不展示内部 trace。

## 5. 排障重点

线上排查优先看：

1. `llm_route`：模型、模态、thinking 配置是否符合预期。
2. `phase_history`：是否经过 route/execute/finalize。
3. `route_trace.route` / `task_type`：需求理解路由是否正确。
4. `route_trace.reasoning_trace.intent_agent_result`：需求理解输出是否低置信、缺字段或含 safety flags。
5. `route_trace.reasoning_trace.update_params`：写操作参数是否来自当前输入/附件。
6. `generated_tool_calls`：是否误调写工具或缺少必要读工具。
7. `check_result`：是否被脱敏、空答、无效链接或参数校验兜底。
8. 最终回复：是否夹带搜索包装文本、工具名或内部信息。

知识链额外看：

- knowledge 工具入参 query 是否过泛或被错误限制站点。
- `retrieval_trace` 是否记录本地 KB、web、browser 各层证据。
- `question_class`、`web_answerability_reason`、`risk_flags` 是否识别教程类证据不足。

## 6. 当前已知限制

- 知识库内容不足时，平台操作类问题会更依赖 web/browser 核验。
- `agent-browser` 是受控公开网页证据层，不处理登录态、Cookie 或站内复杂交互。
- 船名解析仍依赖船舶搜索候选；唯一命中也需要用户确认后才能写入。
