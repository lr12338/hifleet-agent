# HiFleet 客服 Agent 多模态审计

## 当前链路与根因

```mermaid
flowchart LR
  U["用户文字 + 当前附件"] --> P["多模态感知"]
  P --> F["文本与附件融合"]
  F --> S["场景识别"]
  S --> PL["专项 Planner"]
  PL --> T["工具执行"]
  T --> E["Claim 证据裁决"]
  E --> R["回复合同"]
  R --> A["客服回答"]
```

原链路已经有附件提取、直接多模态感知和 `execute_planned_multimodal_chain`，但在轻量客服图中会把图片/视频感知拼接成文字并替换当前用户消息。这会让后续普通 Agent 只能看到代理文本，容易把“截图显示什么”误作“用户要求做什么”。此外，路由仅将图片粗分为 `chart_symbol` 或 `multimodal_understanding`，无法稳定区分指标口径、页面说明、故障、船位事件和明确写入。

## 新架构

- 感知：`MultimodalPerceptionResult` 兼容字段被扩展为 OCR 文本块、页面上下文、可见对象、船舶/指标/错误实体、圈选区域和不确定字段；感知只记录附件可见事实。
- 融合：用户文字仍是任务动作的主依据；原始当前轮图片、音频、视频消息保留在 graph state，不再被感知文字替换。
- 语音写入：音频 `recognized_text`/`audio_transcript` 视为客户自身表达，可触发既有 ship-update 子 Agent；视频摘要只视为观察事实，不能独立授权写入。理解模型返回不完整 JSON 时，媒体 envelope 和确定性 business scenario 仍由 fallback contract 保留。
- 场景：`classify_multimodal_scenario` 输出细粒度 `multimodal_scenario`，覆盖海图符号、UI、指标、排障、船位事件、媒体船舶查询、媒体写入、文件、音频、视频和模糊请求。
- 路由：`refine_multimodal_route_with_perception` 根据结构化 scenario 选择既有知识、船舶、文件或更新链；写入仍由既有 ship-update 子 Agent 的显式请求、字段和确认门禁裁决。
- 证据：`EvidenceItem` 兼容适配器为视觉、用户报告、知识检索和船舶工具结果补齐 `claim`、`verified`、来源、支持/冲突关系；planner trace 同时记录 required-claim 覆盖情况。
- 代理兼容：仅在调用通用 delegate 时生成“附件可见特征”文本 briefing；不修改 state 中的原附件，专用链仍可使用附件和 structured perception。

## 模块职责

| 模块 | 职责 |
|---|---|
| `src/agents/agent.py` | 保留当前轮媒体；执行直接感知；记录媒体保留和输入数量；仅为通用 delegate 生成只读 briefing。 |
| `src/agents/customer_support_understanding.py` | 统一场景理解、工具需求、后台诊断/写入标志和 required claims；平均航速场景分别追踪分母、停泊/锚泊、进出港低速与航次平均速度口径。 |
| `src/agents/customer_support_router.py` | 由 scenario 将请求映射到现有专项工具链，不改变既有 ship-update 安全门禁。 |
| `src/agents/multimodal_contracts.py` | 统一 EvidenceItem 兼容结构、验证边界和 required-claim 覆盖统计。 |
| `src/agents/ship_update_subagent.py` | 继续负责写入字段、身份、确认和工具执行；不因图片出现船舶字段自动写入。 |
| `src/skills/multimodal_support/tools.py` | 仅输出附件安全元数据，绝不作为视觉识别结果。 |
| `scripts/eval_multimodal_customer_support.py` | 从 fixture manifest 执行 contract-only 或显式 direct-graph 评测，脱敏写出结构化 trace。 |

## 风险与安全边界

- `inspect_media_attachment` 的 filename、suffix、category 和分析能力只用于安全元数据，不能推断业务场景或图片含义。
- 故障事件只能生成可继续排查的 IncidentPacket/建议；没有后台诊断工具结果时不得声称“已后台核查”。
- 平台指标与产品规则必须有本地知识库、官方公开页面或浏览器正文证据；不得仅由截图数字或算术推导。
- 图像船名/MMSI 可作为查询候选，但媒体写入必须有明确写入语言，并继续通过 ship-update 字段、身份和确认控制。
- 当前轮媒体保留；历史多媒体继续被压缩，且非船舶 scenario 不复用历史船舶实体。
- 默认评测模式只验证 fixture/理解合同并标记 `not_executed`；只有明确传入 `--direct-graph` 才会标记为真实 Graph 执行，避免把模拟或未执行结果写成真实测试。

## 场景与专项链

| Scenario | 输入类型 | 确定性执行链 | 允许工具 | 禁止行为 |
|---|---|---|---|---|
| `chart_symbol_explanation` | image/video | 感知 → 特征/候选 → 图例/知识检索 → 证据回复 | `local_kb_search`、`web_search`、browser、符号资料 | 按文件名或颜色关键词固定回答；无图例高置信断言 |
| `platform_ui_explanation` | image/video | 页面/控件定位 → 产品知识检索 → 回复条件 | `local_kb_search`、`web_search`、browser | 将页面内船舶字段误作写入命令 |
| `platform_metric_definition` | image/video | 指标/值/单位/时间范围 → 产品口径检索 → 证据裁决 | `local_kb_search`、`web_search`、browser | 仅凭页面数值推导产品公式 |
| `platform_troubleshooting` | image/video | 错误/页面/操作识别 → 已知规则 → 分层排查 | `local_kb_search`、`web_search`、browser | 声称已做后台诊断 |
| `ship_tracking_incident` | image/video/text | 身份提取 → 分船读工具 → IncidentPacket | `ship_search`、`get_ship_position`、`get_ship_archive`、轨迹工具 | 调用 `upload_ship_position` / `update_ship_static_info`；把用户报告当验证事实 |
| `ship_query_from_media` | image/video | 媒体身份提取 → 歧义检查 → 船舶读取 | `ship_search`、船位/档案/PSC 工具 | 以截图字段自动写入 |
| `ship_update_from_media` | image/video | 明确写入请求 → ship-update 子 Agent → 确认/写工具 | 既有 `SHIP_UPDATE_BUNDLE` | 缺明确动词、身份或必填字段时写入 |
| `file_or_document_task` | file + preview | 文件解析 → 文件任务链 | `inspect_customer_file`、产物工具 | 将文件预览当海图符号 |
| `audio_request` / `video_request` | audio/video | 转写/摘要 → 上述 scenario 分类 | 对应媒体感知与专项工具 | 仅按附件类型作最终业务判断 |
| `ambiguous_multimodal` | all | 已识别事实 → 一项关键追问 | 无需高风险工具 | 清晰图片裸拒答或连续追问多个槽位 |
