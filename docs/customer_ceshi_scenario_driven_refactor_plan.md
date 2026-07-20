# customer_ceshi 场景驱动重构：只读分析与实施计划

> 分析日期：2026-07-17。本文记录现状、边界和实施顺序；不改变 `customer_support` 的生产 Builder、状态、Prompt、Router、工具权限、Checkpoint、写入链路或返回格式。

## 1. 当前实际调用链

`agents.agent.build_agent()` 在 `agent_profile=customer_ceshi` 时选择 `agents.customer_ceshi_responses.build_customer_ceshi_responses_agent()`；该运行时使用独立 `customer_ceshi_responses` checkpoint namespace 和本地 `ConversationMemory`。纯文本进入 `NativeToolRuntime`，优先以 Responses `function_call -> function_call_output -> previous_response_id` 循环执行；Responses 不可用时使用 Chat Function Calling。

当前有媒体时，`SingleModelCustomerCeshiRuntime._invoke_multimodal()` 直接把问题、媒体和业务只读工具交给 Doubao Responses 循环。这使 Doubao 同时承担感知、工具选择、证据判断和最终客服答复，违反“DeepSeek 始终为主编排”的目标。

## 2. 当前 Responses 请求与续传

文本 Responses 请求目前包含 `model`、`input`、`tools`、采样/思考选项；本地执行工具后通过 `function_call_output` 与 `call_id` 回传，并使用上一轮 `response.id` 作为 `previous_response_id` 续传。Provider 文档确认该模式、默认存储、流式、思考控制和上下文缓存均为 Responses 能力。

需要保留原始 response ID/call ID，保持同一任务内续传；跨用户轮次不能无限串接 Provider response ID，应持久化受控业务 Session，而不是原始网页正文、签名 URL 或推理内容。

## 3. 文本与多模态职责现状

当前纯文本由配置中的 DeepSeek 模型处理；媒体轮由 Doubao 图像/视频或音频模型处理。Doubao 当前被授予业务检索工具，并直接返回答复。目标改为 DeepSeek 的唯一业务 Orchestrator：DeepSeek 根据附件调用 `inspect_media`；Doubao 仅返回结构化 `PerceptionPacket`（可见/可听事实、OCR、置信度、冲突、限制）；DeepSeek 再选择业务工具、核验证据并输出答案。

## 4. 现有停止条件问题

`NativeToolRuntime._compact_observation()` 仍保留 `can_answer`、`recommended_next_action` 等字段，且 `_can_answer_from()` 可令运行时强制最终回答。这会让工具或固定代码替主模型决定结束。重构后 Observation 仅表达状态、事实、数据、来源、警告和可重试信息；是否继续由主模型及预算决定。

## 5. 上下文、Session 与 Checkpoint

当前本地记忆以 `tenant:user:session` 索引，保存压缩回合；同题工具循环使用 `previous_response_id`。这可以复用，但必须：

- 将 Session key 标准化为 `customer_ceshi:{tenant}:{user}:{session}`；
- 持久化最近会话、确认船舶、未完成 Draft、必要 Evidence 引用和媒体资产引用；
- 限制 Draft 轮数/TTL，并在换船、换任务或新附件时失效；
- 不将 `customer_ceshi` 状态暴露给 `customer_support`。

## 6. 当前写入流程与风险

当前运行时向模型暴露 `update_ship_data_candidate`，并可在模型工具调用中直接导入底层 `upload_ship_position`/`update_ship_static_info`；写入结果以文字中是否出现失败关键词判断成功。媒体证据还会在下一条明确命令时直接触发更新。这不满足确认、事务状态和结果语义要求。

目标是只公开 `prepare_ship_update`、`commit_ship_update`、`cancel_ship_update`。`prepare` 调用确定性 Normalizer 生成带来源的 `ShipUpdateDraft`，永远要求确认；`commit` 仅接受当前 Session 未过期 Draft 的明确确认，并且只有 Adapter `status=success` 才可说“更新成功”。`accepted`、`pending`、`unknown` 必须说明尚未确认完成。

## 7. Prompt 与 Runtime 不一致

Profile 已描述“DeepSeek 编排、Doubao 按需感知、独立确认门”，但 `customer_ceshi_single_model_per_request=true` 及媒体单模型循环仍与之冲突；Doubao 配置也默认开启高思考和较大的输出。重构将该开关默认关闭，拆分 Orchestrator、Perception、Update Policy、Formatter 四层提示，并把 Doubao 设为低思考、结构化、受限输出的感知工具。

## 8. 报告与场景真值风险

现有对话报告、CSV/JSONL 和自动分析是候选案例，不是 Gold Answer。尤其不能从旧 Agent 的“缺少经度”、含有失败字样、任意工具成功、`[image_url]` 占位符或单个更新 turn 推导真值。将新增可复核的 validated case 输出；P0/P1 更新、时间、工具状态、前台/后台意图和产品规则必须独立规则校验或进入人工复核队列。

## 9. 可复用基础设施

- 独立 `customer_ceshi_responses` Builder、配置选择和 checkpoint namespace；
- Responses/Chat Function Calling 客户端创建、工具 schema、流式 API 外壳；
- `CapabilityRegistry`、只读技能、媒体标准输入解析、trace 脱敏和现有 HTTP 兼容层；
- `customer_ceshi_v2` 的 Evidence、模型/合同类型及已有隔离测试；
- 现有船舶查询、知识库、网页核验和媒体模型接入。

## 10. 必须替换或收紧的实现

1. 删除媒体“Doubao 全业务 Agent”路径，改为 DeepSeek 调用 `inspect_media`。
2. 删除 `can_answer`/推荐动作对循环停止的控制权。
3. 以统一 Observation 合同包装全部工具异常、超时、空结果和异步状态。
4. 用确定性坐标、时间、船舶身份和静态字段 Normalizer 替换自由抽取。
5. 用 Draft prepare/confirm/commit 替换直接底层写调用与关键词成功判定。
6. 为产品、权限、来源、执行成功和“无结果”增加 Claim–Evidence Guard。

## 11. 目标运行架构

```text
START -> load_session -> DeepSeek AgentLoop
      -> (inspect_media -> Doubao PerceptionPacket)?
      -> native read-only tools / deterministic update tools
      -> optional approval interrupt
      -> claim-evidence guard -> compact formatter
      -> persist_session -> END
```

LangGraph/运行时只承担 Session、超时、预算、恢复、trace、最终门禁和输出适配；不为台风、符号、会员、ETA 等场景扩展固定业务 Graph 节点。

## 12. 工具白名单与场景合同

首批只读工具：`inspect_media`、本地知识库/网页核验、船舶搜索和只读船舶/港口/地理查询。写入工具仅为 `prepare_ship_update`、`commit_ship_update`、`cancel_ship_update`，不暴露底层上传/静态更新。

场景合同覆盖：船舶查询、船位更新、静态更新、平台功能、会员权限、故障排查、多模态符号/截图和投诉反馈。合同定义输入、允许工具、所需证据、完成条件、禁止断言及答复长度，不作为固定关键词 Router。

## 13. 分阶段实施

1. 建立共享合同：Observation、PerceptionPacket、Evidence、ShipUpdateDraft、工具/场景注册表。
2. 实现确定性字段 Normalizer 与 Draft 仓储、确认令牌和 Adapter 状态转换。
3. 用 DeepSeek-led Responses AgentLoop 替换媒体单模型循环，并保留经探测验证后的 Chat fallback。
4. 接入 Claim–Evidence Guard、格式化、指标和安全脱敏。
5. 从报告生成 validated case / matrix，补齐单元、HTTP、Session、流式和隔离回归。
6. 运行能力探测、真实服务 E2E 与 OSS 媒体测试；未实际运行项目明确标记 `NOT_RUN` 或 `SKIPPED`。

## 14. 风险、回滚与验证边界

- Provider 字段或模型能力不一致：通过真实 capability probe 选择 `responses`、`chat_function_calling` 或独立安全错误，不回退 `customer_support`。
- 外部写入风险：默认 Fake/dry-run Adapter；无明确隔离环境、测试船舶和确认绝不真实 commit。
- 检索证据不足：删除/弱化 Claim 或只说明已证实部分，不能补造前台功能、套餐或来源。
- 回滚：保持 `customer_ceshi` 的独立运行模式和旧代码边界；不触碰 `customer_support`。

## 15. customer_support 不可修改清单

不得修改 `customer_support` 的 Builder、State、Prompt、Router、工具权限、Checkpoint、写入链路、HTTP 返回格式及既有测试预期。最终以路径级 diff 和完整既有回归证明零影响。
