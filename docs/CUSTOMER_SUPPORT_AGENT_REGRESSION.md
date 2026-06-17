# Customer Support Agent Regression

本文描述当前 `customer_support` 主链的回归范围、测试矩阵、验收标准和线上排障重点。

## 1. 当前回归范围

当前主链不再是单纯的 `route -> delegate -> check -> finalize`，而是：

1. `route -> execute -> check -> finalize`
2. 前置安全拦截
3. 附件/截图轻量 perception
4. 轻量 intent agent 结构化路由判断
5. deterministic guard 修正安全、写操作、明确船舶/文件等高风险场景
6. planner JSON 结构化理解
7. deterministic harness 执行链
8. 标准客服 Agent delegate 兜底
9. `smart_search` 与 `agent_browser_deep_search` 的受控知识检索和官方页面核验
10. 船舶查询、统计、写操作、文件、多模态、网页核验链路
11. 多轮上下文压缩与相关历史筛选
12. 最终输出脱敏与链接校验
13. `/stream_run` 调试流事件

## 2. 当前测试入口

全量客服相关最小回归：

```bash
PYTHONPATH=src .venv-test/bin/python -m pytest -q \
  tests/test_customer_support_router.py \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_stream_debug.py
```

本轮与当前主链最相关的两组：

```bash
./.venv-test/bin/python -m pytest tests/test_customer_support_router.py -q
./.venv-test/bin/python -m pytest tests/test_customer_support_intent_agent.py -q
```

每次上线前应记录当前远端实际结果，不要沿用历史 passed 数。若服务器依赖环境暂时无法跑 pytest，至少执行 `python3 -m py_compile` 检查核心客服链路文件，并在发布记录中说明 pytest 阻塞原因。

## 3. 主链验收标准

一次成功客服请求通常应满足：

1. `phase_history` 包含：
   - `route -> executed -> check -> done`
   - 或 `route -> delegated -> check -> done`
2. `route_trace.run_id` 与外层 API `run_id` 一致
3. `generated_tool_calls` 与真实执行工具一致
4. `check_result` 能反映：
   - `has_answer`
   - `links_ok`
   - `post_guard_applied`
   - `evidence_count` 或实体/上下文校验结果
5. 最终 `messages[-1].content` 已经过 `sanitize_customer_output(...)`
6. 调试态可看到 `route_trace.reasoning_trace`，普通用户回复中不能出现该 trace

## 4. 当前重点验收场景

### 4.1 知识问答

- `smart_search` 高置信命中时，不应再触发不必要的 `agent_browser_deep_search`
- 用户要求验证官方/社区/今日/最新内容时，即使 `smart_search` 有泛化结果，也应触发 browser 官方核验
- `agent_browser_deep_search` 返回结构化 evidence 后，最终回复不能暴露内部 CLI、日志、路径、JSON

### 4.2 多轮上下文

- 新问题不应被无关历史误导
- “上面 / 上一条 / 这艘船 / 总结”类追问应能复用相关历史
- intent/planner 看到的上下文应来自压缩后的相关窗口，而不是原始长历史

### 4.3 轻量 perception + intent agent 路由

- `这个圆圈是什么` + HiFleet 海图截图，应优先基于 `perception_result` 判断为 `chart_symbol` 或 `platform_knowledge`
- `请分析这张图片` + Error 弹窗，应优先判断为 `platform_troubleshooting`
- 低置信截图识别不应进入长流程，应追问一项关键补充
- `route_trace.reasoning_trace.route_source` 应能看出最终来自 `light_agent`、`write_guard`、`safety_rule` 或 `fallback_rule`

### 4.4 Harness 路由

- `knowledge`、`chart_symbol`、`multimodal_understanding`、`conversation` 可进入 planner 直答链
- `ship_single`、`ship_complex`、`ship_stats`、`ship_update`、`file_task`、`browser_verify` 应优先走 deterministic harness
- 不满足 harness 条件时，允许回退 delegate

### 4.5 关键业务负例

- `我是免费用户，为什么在网站上看不到最新的船位？`
  - 应解释 HiFleet 免费账号/数据延迟/权限，不应返回随机船舶坐标。
- `验证 注意！浏览器开始记忆船队“筛选”了 的详细内容`
  - 应核验具体官方社区文章，不应只返回社区首页或帮助中心首页。
- `SUNNY STAR历史轨迹是哪些`
  - 若上下文已有 `SUNNY STAR / MMSI`，只追问起止时间，不追加无关广告。
- `你们这网速太卡了，我电脑都死机了`
  - 应先确认是否发生在 HiFleet 页面和具体操作，不应强行输出完整平台排障模板。
- `这个圆圈是什么`
  - 无截图或前文上下文时，轻量确认是否在 HiFleet 地图/海图页面看到；有截图时按截图内容处理。

### 4.6 输出清洗

最终回复不能包含：

- `综合摘要`
- `查询1`
- `[HTMLLINK_0]`
- `下载APP,手机查船更方便`
- `smart_search`
- `agent_browser_deep_search`
- `reasoning_trace`
- 内部路径、token、env、JSON 包装文本

## 5. 流式调试验收

`/stream_run` 当前验收点：

- 能看到：
  - `message_start`
  - `thinking`
  - `tool_response`
  - `answer`
  - `message_end`
- 事件内容要体现：
  - 前置安全
  - 附件 perception 摘要
  - 路由判断
  - 轻量 intent agent 结果摘要
  - execute / delegate 分支
  - 附件输入分析
  - 后置内容质检
- 不能体现：
  - prompt 原文
  - 隐藏 chain-of-thought
  - 内部路径
  - key / token / env

## 6. 线上排障重点

排查当前 `customer_support` 线上问题时，优先看：

1. `route_trace.route` / `route_trace.task_type`
   - 是否误分流
2. `route_trace.reasoning_trace.route_source`
   - 是轻量 Agent 判断，还是安全/写操作/fallback 规则接管
3. `route_trace.reasoning_trace.perception_summary`
   - 附件识别是否为空或置信度过低
4. `phase_history`
   - 这次请求到底走了 `execute` 还是 `delegate`
5. `generated_tool_calls`
   - 是否调用了预期工具
6. `route_trace.fallback_reason`
   - 是否因为 `smart_search_empty_agent_browser_fallback`、`unsupported_*` 等原因降级
7. `check_result`
   - 是否被脱敏、空答、无效链接兜底
8. `check_result.evidence_summary`
   - 是否存在官方页面证据
9. 最终回复
   - 是否仍夹带搜索包装文本或内部信息
10. `latency_hotspot.total`
   - 是否存在异常慢请求

## 7. 当前已知限制

- 知识库内容不足时，`smart_search` 命中质量会直接下降
- `agent-browser` 是受控官方核验证据层，不是自由浏览器 Agent
- `agent-browser` 当前只抓取公开网页正文，不处理登录态、Cookie、站内复杂交互
- 多轮上下文目前以压缩摘要和相关历史筛选为主，不是完整长链记忆回放
- `reasoning_trace` 是审计摘要，不是隐藏思维链；不能原样展示给普通用户

## 8. 当前优化优先级

1. 补知识库内容
2. 补官方网页抓取与索引覆盖
3. 提升轻量 intent agent 与 planner/query rewrite 的稳定性
4. 扩展异地部署联调脚本和线上观测面板
5. 不优先恢复旧版重型 Planner/Harness 设计稿
