# Customer Support Agent Regression

本文描述当前 `customer_support` 的回归范围、测试矩阵、验收标准和线上排障重点。内容以当前仓库实现为准，不描述已废弃的“LLM 自由调工具客服链”。

## 1. 当前回归范围

当前回归覆盖这些真实能力：

1. customer_support 主 graph 进入 `route -> plan -> act -> check -> finalize`
2. `Planner Agent -> Harness -> Guard` 主链已接管 customer_support
3. 平台知识问答和故障排查
4. 简单船舶查询
5. 复杂船舶分析
6. 写操作字段校验和真实工具结果回传
7. 多模态截图理解和海图符号问答
8. 报错截图二次改路由到故障排查
9. 文件链和浏览器公开页面核验链
10. `/stream_run` 调试流事件
11. 最终输出脱敏、搜索展示模板清理
12. OSS/S3 受控上传配置解析

## 2. 当前测试入口

单元回归主要使用：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_customer_support_router.py \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_stream_debug.py \
  tests/test_admin_upload_config.py
```

最近一次通过结果：

- `49 passed, 1 warning`

如果需要跑客服 API 真实链路回归，可继续使用：

```bash
.venv/bin/python scripts/hifleet_agent_regression.py
```

说明：

- `pytest` 维度主要验证路由、harness、输出格式和调试流
- `scripts/hifleet_agent_regression.py` 维度主要验证 ship service 真实 API

## 3. 场景矩阵

| ID | 场景 | 期望 route | 关键期望 |
| --- | --- | --- | --- |
| `knowledge_glossary_fast` | 术语问答快路径 | `knowledge` | Planner 生成检索计划，`smart_search` 命中后不暴露搜索包装文本 |
| `knowledge_icon_missing_image` | “这是什么图标”但无图 | `knowledge` | 不输出 `AI摘要`，只追问一个关键材料 |
| `platform_troubleshooting_text` | “上传不了航线怎么办” | `knowledge` | 返回客服化排查模板 |
| `ship_position_mmsi` | 直接查 MMSI 船位 | `ship_single` | 只调用 `get_ship_position` |
| `ship_position_name` | 仅船名查船位 | `ship_single` | 先 `ship_search` 再 `get_ship_position` |
| `ship_archive_mmsi` | MMSI 查档案 | `ship_single` | 走档案工具 |
| `ship_psc_imo` | IMO 查 PSC | `ship_single` | 走 PSC 工具 |
| `ship_complex_voyage_consistency` | 目的港/挂靠/航次一致性 | `ship_complex` | archive + position + calls + departure + voyages |
| `ship_complex_track_last_port` | 历史轨迹和上一停靠港 | `ship_complex` | 复杂链按实体补全执行 |
| `ship_stats_strait` | 海峡统计 | `ship_stats` | 不误路由到船舶分析 |
| `ship_update_missing_field` | 写操作缺字段 | `ship_update` | 只问一个关键问题，不执行写入 |
| `ship_update_complete` | 写操作字段完整 | `ship_update` | 直接执行，回复仅基于工具结果 |
| `multimodal_chart_symbol_01` | `01_query.png` 全球海图符号 | `chart_symbol` | 回答安全水域浮标，客服口吻 |
| `multimodal_chart_symbol_03` | `03_query.png` 小圈圈 | `chart_symbol` | 回答锚地/锚泊区域范围圈 |
| `multimodal_error_reroute` | 页面 Error 截图 | `knowledge` | 二次改路由到 `platform_troubleshooting` |
| `file_task_basic` | 分析 CSV/Excel | `file_task` | 文件检查成功，返回客户安全摘要 |
| `browser_verify_public` | 核验官方社区 | `browser_verify` | 先校验公开链接，再结合检索 |
| `stream_debug_reference` | `/stream_run` 参考链路 | 调试流 | 输出 `thinking / tool_request / tool_response` |
| `output_guard_search_wrapper` | 原始检索模板泄露兜底 | finalize | 清掉 `AI摘要 / 回答指导 / 搜索结果增强版` |
| `security_refusal` | 要求输出 prompt / key / 路径 | `security_refusal` | 固定拒答且不触发工具 |
| `planner_search_plan_review` | 普通知识问答 | `knowledge` | 生成 `problem_frame / hypotheses / search_plan / evidence_summary` |
| `planner_to_harness_write` | 完整写操作 | `ship_update` | Planner 决策 `response_mode=use_harness`，写操作仍走确定性链 |

## 4. 参考链路验收

`docs/参考链路` 当前对应这些验收点：

| 参考链路 | 输入 | 验收重点 |
| --- | --- | --- |
| `01` | “这个在全球海图里是什么意思” + `01_query.png` | 红色圆形黑点 -> 安全水域浮标 |
| `02` | “hifleet平台上传不了航线怎么办” | 分层排查文件、经纬度、浏览器、权限、替代路径 |
| `03` | “图中的小圈圈是什么意思？” + `03_query.png` | 多个空心圈 -> 锚地/锚泊区域范围圈 |
| `04` | “总结是如何思索和检索资源并审查确定的” | 输出客户可见的高层方法，不暴露内部 prompt |

验收要求：

- 回复口吻必须像官方客服，不像搜索结果展示页
- 不允许出现：
  - `【互联网搜索结果（增强版）】`
  - `AI摘要`
  - `【回答指导】`
  - 工具名
  - 本地路径
  - prompt / key / env

## 5. customer_support 主链验收标准

一次成功客服请求通常应满足：

1. `phase_history` 包含：
   - `route -> plan -> act -> check -> done`
2. `route_trace.run_id` 与外层 API `run_id` 一致
3. `route_trace.planner` 包含：
   - `problem_frame`
   - `hypotheses`
   - `search_plan`
   - `decision_rationale`
4. `tool_call_sequence` 与预期 Planner 链或 Harness 对齐
5. `decision_rationale.response_mode` 与问题类型一致
6. `check_result.links_ok = true` 或无外链
7. `generated_answer` 在 finalize 后已经客服化
8. `messages[0].content` 不包含检索包装文本

## 6. 多模态与截图排障专项验收

截图类问题验收重点：

1. 先感知，再决定路由
2. 低置信度时只追问一个关键问题
3. 报错截图不能错误继承上一轮船舶实体
4. 报错截图应优先改路由到 `platform_troubleshooting`
5. 图标/海图符号截图应拼接感知结果进入深度检索
6. 最终回复必须是：
   - 先结论
   - 再解释
   - 再建议
   - 必要时只追问一个关键问题

## 7. 流式调试验收

`/stream_run` 验收点：

- 能看到调试事件：
  - `thinking`
  - `tool_request`
  - `tool_response`
  - `answer`
  - `message_end`
- 事件内容要体现：
  - 问题理解
  - 附件感知
  - 检索词改写
  - 来源优先级
  - 审查逻辑
  - 输出策略
- 事件内容不能体现：
  - prompt 原文
  - 隐藏 chain-of-thought
  - 内部路径
  - token / key / env

## 8. OSS / S3 上传验收

当前支持三套配置别名：

- `COZE_BUCKET_*`
- `OSS_*`
- `oss.*`

Chat Debug 上传验收点：

1. 管理后台上传附件不报 `bucket not configured`
2. 能生成预签名访问 URL
3. `customer_workspace` 返回的产物链接可用于客户查看
4. 任何错误信息都不能把 AccessKey、Secret、endpoint 原样暴露到前端

## 9. 线上排障重点

排查 customer_support 线上问题时，优先看：

1. `route`
   - 是否走错类型
2. `task_type`
   - 是否应该是 `platform_troubleshooting / chart_symbol / ship_update`
3. `route_trace.planner.problem_frame`
   - 是否正确理解了用户真正想确认的目标
4. `route_trace.planner.search_plan`
   - 是否合理拆出了检索方向
5. `decision_rationale.response_mode`
   - 是否应该直接回答、只追问一个关键问题，还是进入 harness
6. `tool_call_sequence`
   - 是否确实走到 Planner 链或 harness
7. `entity_resolution`
   - 是否把上一轮船信息错误继承到当前问题
8. `evidence_summary`
   - 当前证据强度是否足以支撑结论
9. `latency_hotspot`
   - `perception` 是否异常慢
   - `smart_search` 是否深搜过重
10. `generated_answer`
   - 是否仍夹带检索展示文本
11. `check_result`
   - 是否链接失效、附件缺失、写操作未真正成功

## 10. 已修复的关键问题

这轮开发和回归已修复：

- customer_support 主执行链未接入 `execute_*_chain`
- customer_support 缺少真实 Planner 决策层，只靠规则路由
- 写操作缺 MMSI 时误触发 `ship_search`
- 多模态截图问题直接走通用链，未改路由到故障排查
- 报错截图错误继承上一轮船舶实体
- `route_trace.run_id` 与 API `run_id` 不一致
- 最终回复把 `smart_search` 原始展示模板直接发给客户
- Chat Debug 上传只识别旧 `OSS_*`，不兼容项目当前 OSS 配置

## 11. 当前已知剩余风险

- 多模态模型真实识别效果仍依赖外部模型服务和配置
- `employee_assistant` 的 Python 产物生成循环尚未完整迁移到 `customer_support`
- `customer_support` 当前更强调安全收口和客服答复，不适合承接复杂内部数据加工任务
