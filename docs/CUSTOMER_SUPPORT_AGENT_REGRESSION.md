# Customer Support Agent Regression

本文描述当前 `customer_support` 轻量全模态 skills agent 的回归范围、测试矩阵、验收标准和线上排障重点。

## 1. 当前回归范围

当前主链为：

```text
preprocess -> delegate standard skills agent -> finalize
```

其中 ship_update 是轻量 graph 的 prompt-driven 子 agent 特殊分支：

```text
preprocess -> multimodal perception -> ship_update subagent -> tool whitelist -> execute or standard-agent handoff -> finalize
```

回归重点：

1. 前置安全拦截。
2. 文本、语音、图片、视频当前轮多模态 direct perception。
3. 文本模型 `deepseek-v4-flash-260425`、多模态模型 `doubao-seed-2-0-lite-260428` 与 `thinking_type=enabled`、`reasoning_effort=medium`。
4. 模型自主调用 `knowledge_qa`、`browser_verify`、`hifleet_ship_service`、`multimodal_support`。
5. `knowledge_qa` 按 `local_kb_search -> web_search -> web_search_agent_browser` 顺序受控升级。
6. 平台操作/问题反馈类问题的多关键词检索和证据充分性复核。
7. 授权知识库维护 `knowledge_admin.upsert_local_kb_entry`。
8. HiFleet 官方社区、帮助中心、官网公开页面核验。
9. 船舶查询、档案、PSC、轨迹、挂靠、统计、船位上传、静态信息更新。
10. 船舶写操作的 `ship_update` 子 agent 结构化计划、`ship_update_draft`、显式意图和必填字段保护。
11. 多轮上下文与最近船舶实体记忆。
12. 最终输出脱敏、链接抽取和 `output_assets`。
13. `/run`、`/stream_run` 和微信旧 `content.query.prompt` 兼容。

旧 `customer_support_router.py`、旧 planner/review/harness 和旧 `_build_customer_support_agent()` 不再承载当前 customer 的通用知识主链。ship_update 写请求当前由 `ship_update` 子 agent 生成结构化计划，主链路只负责工具白名单、真实工具调用、工具结果判定和 trace。

Profile 选择只看请求体 `agent_profile` 或请求头 `x-agent-profile`；`source_channel` 只用于日志和后台筛选，不参与运行时 Profile 判断。未传合法 Profile 时默认 `customer_support`。

## 2. 当前测试入口

客服相关最小回归：

```bash
PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py
PYTHONPATH=src .venv/bin/python scripts/test_llm_config.py
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_router.py \
  tests/test_smart_search_tools.py \
  tests/test_knowledge_admin_tools.py
```

语法级检查：

```bash
PYTHONPATH=src .venv/bin/python -m py_compile \
  src/agents/agent.py \
  src/agents/customer_support_guard.py \
  src/llm_config.py
```

前端模型配置页：

```bash
cd frontend
npm run build
```

每次上线前应记录当前远端实际结果，不要沿用历史 passed 数。若服务器依赖环境暂时无法跑 pytest，至少执行 `py_compile` 和接口烟测，并在发布记录中说明 pytest 阻塞原因。

## 3. 主链验收标准

一次成功客服请求通常应满足：

1. `route_trace.route` 为 `lightweight_skills_agent`。
2. `phase_history` 包含 `preprocess`、`delegate`、`finalize`。
3. 多模态输入时，`route_trace.reasoning_trace.pipeline` 包含 `multimodal_input_parse`。
4. `generated_tool_calls` 与真实执行工具一致。
5. `check_result` 能反映输出清洗、链接安全和旧 router bypass 状态。
6. `response_modalities` 至少包含 `text`；有链接时可包含 `link`。
7. `output_assets` 只包含可给客户看的公开链接或图文链接。
8. 最终 `messages[-1].content` 已经过 `sanitize_customer_output(...)`。
9. 普通用户回复中不能出现 prompt、tool registry、reasoning trace、JSON、内部路径、key/token。

如果是 ship_update 写请求，验收还应满足：

1. `route_trace.route` 可为 `ship_update`。
2. `route_trace.reasoning_trace.ship_update_subagent.status` 应能解释执行、追问、取消、错误或 non-write handoff。
3. `reasoning_trace.understanding_result.operation_type`、`ship_update_candidate`、`pending_action`、`non_write_reason` 只作为 hint，不是最终写入许可。
4. `ship_update_draft`、`write_args`、`missing_required_fields` 应足以解释是否真正进入写工具。
5. `write_args` 必须是 skills 工具参数格式，而不是 API body：动态更新用 `draft/navstatus`，静态更新用 `ship_name/imo/ship_type/built_year/draft`。
6. 经纬度、时间和带单位数值必须在执行前格式化：度分坐标转十进制度，时间补齐到 `yyyy-MM-dd HH:mm:ss`，`0 kn / 163° / 1.6 m` 转纯数值，ETA 中的 `(UTC)` 清理为标准时间。
7. 缺字段拦截时 `generated_tool_calls=[]`；`non_write` 应交回 standard agent 排障/知识回答，不得调用写工具。

## 4. 当前重点验收场景

### 4.1 知识问答

- 本地 FAQ 强命中时，应优先使用 `local_kb_search`。
- `web_search` 命中具体事实页时，不应再触发不必要的 browser。
- 用户要求验证官方/社区/今日/最新内容时，应触发 browser 或公开页面核验。
- `agent_browser_deep_search` 返回结构化 evidence 后，最终回复不能暴露内部 CLI、日志、路径、JSON。
- 产品问题才允许 HiFleet 站点过滤；公共权威数据问题不能被错误改写成 HiFleet 产品搜索。
- 平台操作类问题应生成 3 到 5 组关键词，并覆盖入口、动作、保存/完成条件。
- 仅命中帮助中心首页、社区目录、视频标题页、泛功能简介时，不能输出完整教程。
- `怎么绘制区域标注` 应优先命中本地结构化 FAQ，回复包含主海图入口、绘制动作和保存动作。
- `怎么添加电子围栏报警` 应能覆盖我的标注、报警入口、规则、对象和通知方式。
- `标注不保存怎么办` 应区分已确认保存动作、可能原因、检查项和需补充信息，不直接断言根因。

### 4.1.1 授权知识库维护

- 明确 `添加知识库：`、`纠正知识库：` 或 `更新知识库：`，且授权 key 正确时，才允许调用 `upsert_local_kb_entry`。
- `customer_support` 与 `customer_ceshi` 均可写库，但必须通过工具层 profile 和 key 校验。
- 缺正文 `key: ...` 时，应拒绝写入；只传 `x-kb-update-key` header 也应拒绝。
- 工具调用必须保留完整 `raw_text`；模型把 key 拆到其它参数但 `raw_text` 缺 key 时，应拒绝。
- 多行 `名称：描述` 映射表应自动拆成多条独立知识，重复条目跳过，新条目继续写入。
- 普通“你答错了/应该是...”但没有明确写库指令时，不应写入。
- 重复 question 或高度相似内容不应重复追加。
- 写入后 JSONL 每行应可 `json.loads`，并且 `local_kb_search` 可命中新条目。

### 4.2 多轮上下文

- 新问题不应被无关历史误导。
- “上面 / 上一条 / 这艘船 / 总结”类追问应能复用相关历史。
- 船舶追问可复用最近 MMSI、船名等实体。
- 待确认写操作应等待用户补充，不应擅自执行。

### 4.3 多模态预处理

- `这个圆圈是什么` + HiFleet 海图截图，应优先基于 `perception_summary` 判断为海图/地图页面问题。
- `请分析这张图片` + Error 弹窗，应优先判断为平台排障问题。
- 语音输入应先转写或摘要，再回答。
- 视频输入应把可见界面、动作、报错摘要纳入当前轮问题。
- 低置信截图识别不应进入长流程，应只追问一项关键补充。

### 4.4 船舶读写

- 查询船位、档案、PSC、轨迹、挂靠和统计时，可调用对应读工具。
- 明确“上传/更新/修改/补录船位”时，可调用 `upload_ship_position`。
- 明确“更新/修改静态信息”时，可调用 `update_ship_static_info`。
- 用户只说“更新船位”时，只走动态更新；图片里同时出现 `呼号 / AIS船名 / 船型` 时，不能自动切到静态更新。
- 动态更新缺 `mmsi / lon / lat / updatetime` 任一项时，必须在解析层直接返回缺字段提示，`generated_tool_calls=[]`。
- 动态更新中 `船艏/航迹向: A / B` 必须解析为 `heading=A`、`course=B`，不得把二者当作同一字段冲突。
- 动态更新中 `目的港/ETA: -- / --`、`/ETA`、`ETA` 等占位符不得进入 draft、`write_args` 或最终成功回复。
- 动态更新中残缺 ETA 不应导致写入失败；无法归一的 ETA 作为可选字段丢弃。
- 静态更新船型/船舶类型时，必须同时传 `ship_type` 与 `minotype` 且值一致。
- 缺少 MMSI、经纬度、更新时间或静态字段等必要信息时，只追问一个最关键字段。
- 工具未返回成功时，不得宣称已更新成功。
- “为什么不更新 / 更新慢 / 看不到最新船位”是解释或排障，不是写操作。
- `IMO` 唯一命中可补全 MMSI；`船名` 唯一命中仍需确认，不直接写入。

### 4.5 微信旧接口

- 微信客服旧 `/run` 请求中 `content.query.prompt` 应被归一化为 `messages`。
- `type=text`、`image`、`voice`、`video` 应分别映射到文本、`image_url`、`input_audio`、`video_url`。
- 微信调用方应显式传 `agent_profile=customer_support`；缺省会回退 `customer_support`，但不再通过 `source_channel=wechat_kf` 或 `wechat_mp` 决定 Profile。
- 回复必须可直接发给微信用户，不展示内部 trace。

### 4.6 关键业务负例

- `我是免费用户，为什么在网站上看不到最新的船位？`
  - 应解释 HiFleet 免费账号/数据延迟/权限，不应返回随机船舶坐标。
- `今日长江水位`
  - 应走公共权威数据检索，不应落回 HiFleet 社区首页。
- `智能视频监控`
  - 若 web 已命中具体官方页，应停止，不要再盲目升级 browser。
- `验证 注意！浏览器开始记忆船队“筛选”了 的详细内容`
  - 应核验具体官方社区文章，不应只返回社区首页或帮助中心首页。
- `SUNNY STAR历史轨迹是哪些`
  - 若上下文已有 `SUNNY STAR / MMSI`，只追问起止时间，不追加无关广告。
- `你们这网速太卡了，我电脑都死机了`
  - 应先确认是否发生在 HiFleet 页面和具体操作，不应强行输出完整平台排障模板。
- `这个圆圈是什么`
  - 无截图或前文上下文时，轻量确认是否在 HiFleet 地图/海图页面看到；有截图时按截图内容处理。
- `我司2艘船在 BAY OF BENGAL 连续1-2天没有船位跟踪，AIS 工况正常，请后台看看什么问题`
  - 属于排障/知识咨询，不应误进 ship_update。
  - 若截图 OCR 中出现 `更新于`、`暂未收到更新船位`、`船位报告` 等词，也不能仅凭这些词当作写请求。

### 4.7 输出清洗

最终回复不能包含：

- `综合摘要`
- `查询1`
- `[HTMLLINK_0]`
- `下载APP,手机查船更方便`
- `smart_search`
- `agent_browser_deep_search`
- `reasoning_trace`
- prompt / tool registry
- 内部路径、token、env、JSON 包装文本

## 5. 流式调试验收

`/stream_run` 当前验收点：

- 能看到：
  - `message_start`
  - `thinking`
  - `tool_response`
  - `answer`
  - `message_end`
- 事件内容可体现：
  - 前置安全
  - 多模态 perception 摘要
  - skills 工具调用
  - 输出质检
- 不能体现：
  - prompt 原文
  - 隐藏 chain-of-thought
  - 内部路径
  - key / token / env

## 6. 线上排障重点

排查当前 `customer_support` 线上问题时，优先看：

1. `llm_route`
   - 是否使用预期模型、模态和 thinking 配置。
2. `route_trace.route`
   - 是否为 `lightweight_skills_agent`。
3. `route_trace.reasoning_trace.pipeline`
   - 是否经过多模态预处理。
4. `route_trace.reasoning_trace.perception_summary`
   - 附件识别是否为空或置信度过低。
5. `phase_history`
   - 是否包含 `preprocess/delegate/finalize`。
6. `generated_tool_calls`
   - 是否调用了预期工具，是否误调写工具。
7. `response_modalities` / `output_assets`
   - 链接是否适合客户可见。
8. `check_result`
   - 是否被脱敏、空答、无效链接兜底。
9. 最终回复
   - 是否仍夹带搜索包装文本或内部信息。
10. `latency_hotspot.total`
   - 是否存在异常慢请求。
11. `route_trace.reasoning_trace.instruction_text`
    - 当前轮文字意图是否被 OCR 重写文本冲淡。
12. `route_trace.reasoning_trace.parsed_dynamic_fields`
    - 解析层到底识别到了哪些动态字段。
13. `route_trace.reasoning_trace.field_sources`
    - 字段来自文本还是附件。
14. `route_trace.reasoning_trace.resolved_identifier`
    - 本轮最终用于写入的船舶标识是什么。
15. `route_trace.reasoning_trace.write_args`
    - 最终准备传给工具的参数是否完整。
16. `route_trace.reasoning_trace.missing_required_fields`
    - 是真实缺字段，还是误路由后自然缺字段。

知识链额外看：

11. knowledge 工具入参 query
    - 是否被错误限制到 HiFleet 站点。
12. `retrieval_trace`
    - 是否记录了本地 KB、web、browser 各层证据。
13. `t1_payload_meta` / `request_profile`
    - 是否出现错误 `Sites` 污染或 Ark fallback 覆盖。
14. `question_class`、`web_answerability_reason`、`risk_flags`
    - 教程类问题是否被识别为需要更严格证据。
15. `knowledge_admin` 工具结果
    - 写库是否因缺 key、重复、缺标准答案被正确拒绝。

## 7. 当前已知限制

- 知识库内容不足时，平台操作类问题会更依赖 web/browser 核验；不要用半相关摘要拼完整教程。
- `agent-browser` 是受控官方核验证据层，不是自由浏览器 Agent。
- `agent-browser` 当前只抓取公开网页正文，不处理登录态、Cookie、站内复杂交互。
- 当前已取消自定义上下文压缩摘要；完整文本历史交给 agent/checkpointer 处理，历史多模态内容只做安全脱敏。
- v1 多模态输出只返回文本和链接型图文信息，不生成语音 URL。
- `reasoning_trace` 是审计摘要，不是隐藏思维链；不能原样展示给普通用户。

## 8. 当前优化优先级

1. 补 `docs/RAG` 和本地 KB 命中质量。
2. 优化 customer profile prompt 对工具选择、写操作确认和公共权威数据检索的约束。
3. 收紧 `query_type -> Sites` 约束，避免公共数据检索被 HiFleet 站点污染。
4. 补官方网页抓取与索引覆盖。
5. 扩展异地部署联调脚本和线上观测面板。
