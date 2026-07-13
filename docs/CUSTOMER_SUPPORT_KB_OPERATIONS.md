# customer_support 知识检索与知识库维护总览

本文汇总当前正式客服 `customer_support` 的知识检索、平台操作类问题收口、授权写库和回归验证规则。它是 `KNOWLEDGE_BASE_GUIDE.md` 的运维版入口，面向接入、客服运营和排障。

## 1. Profile 语义

| Profile | 当前语义 |
| --- | --- |
| `customer_support` | 正式客服 profile，面向外部客户、微信客服、WebSDK、CRM。 |
| `employee_assistant` | 兼容别名，运行时会规范化为 `customer_support`。 |
| `customer_ceshi` | 测试/内部 profile，保留 employee workspace、表格检查和受控 Python 能力。 |

日志里判断线路时，优先看规范化后的 `agent_profile`、`route_trace.route` 和工具调用序列。传入 `employee_assistant` 后，预期应落到 `customer_support` 的轻量客服链。

## 2. 平台操作与问题反馈检索原则

客服遇到“怎么操作、入口在哪、怎么设置、为什么不显示、保存不了怎么办”这类问题时，不应只用单个关键词或单条搜索摘要直接收口。

当前要求是：

1. 先拆需求证据面，例如入口、步骤、保存/完成条件、管理/报警、常见异常。
2. 默认生成不超过 3 组短关键词，多轮调用 `local_kb_search` / `web_search`；优先改善 query 和结果重排，不靠盲目增加 Count。
3. 如果 `web_search` 只命中帮助中心首页、社区目录、视频标题页或泛功能介绍页，不能视为可直接回答。
4. 需要正文核验时，升级 `web_search_agent_browser` 或公开页面核验工具。
5. 最终回答前检查证据是否覆盖关键块；缺关键块时只回答已确认内容，并说明需要进一步核验或追问一个关键细节。

对编号、代码、简称等语义不完整输入，不根据格式直接判定编号类型或支持性。需求理解 agent 应先给出 `user_goal`、`rewritten_user_need`、`search_query_candidates` 和 `evidence_required`；当 `evidence_required=true` 时，轻量客服 graph 直接进入知识链。此类请求在工具可用时不会因本地 KB 或网页单层命中提前结束，而是继续完成网页与页面核验。证据不足或冲突时，只使用 `missing_slot` 追问一个关键问题，不能把“未命中”说成“不支持”。

平台教程类完整回答至少需要：

- 入口位置
- 关键操作动作
- 完成/保存条件

问题反馈类回答必须区分：

- 已确认规则
- 可能原因
- 建议检查项
- 仍需用户补充的信息

## 3. 本地知识库优先级

平台产品和操作教程优先沉淀在本地知识库：

- 结构化 FAQ：`docs/RAG/hifleet_cs_outputs/客服知识库结构化.jsonl`
- 标准话术/问答对：`docs/RAG/hifleet_cs_outputs/`
- 背景 wiki：`docs/RAG/hifleet_cs_wiki/`
- 原始手册：`docs/RAG/raw/`

高频操作问题建议采用“结构化 FAQ + wiki 背景”的组合：FAQ 给可直接回答的步骤，wiki 保留功能背景和上下文。

## 4. 授权写入知识库

`customer_support` 和 `customer_ceshi` 均可通过 `knowledge_admin` skill 调用 `upsert_local_kb_entry`，但必须同时满足：

- 用户明确输入 `添加知识库：`、`纠正知识库：` 或 `更新知识库：`
- 内容包含可沉淀的标准答案、适用问题或明确知识点
- 授权 key 与环境变量 `HIFLEET_KB_UPDATE_KEY` 匹配

授权 key 只支持正文传入：

- 正文：`添加知识库：... key: <HIFLEET_KB_UPDATE_KEY>`

`x-kb-update-key` 请求头不再作为写库授权来源；如果请求头中带该 key，工具会拒绝写入。

工具最终写入：

```text
docs/RAG/hifleet_cs_outputs/客服知识库结构化.jsonl
```

如果设置了 `HIFLEET_KB_JSONL_PATH`，则写入该变量指定的 JSONL 文件。工具会做 profile 校验、key 校验、重复检测、JSONL 校验和本地 KB 缓存刷新。

不要把 key 写入文档示例、prompt、日志截图或客户回复。

调用工具时必须把完整用户写库正文放在 `raw_text` 中，包括 `key: ...`。不要把 key 单独拆成其它参数，也不要从 `raw_text` 里删除 key。

批量映射类内容会自动拆条。例如：

```text
更新知识库：
图标名称：图标描述
泊位图标：紫色点线空心圆圈，中心灰绿色实心小点
锚泊区：紫色虚线围成闭合多边形，内部带锚形符号
key: <HIFLEET_KB_UPDATE_KEY>
```

工具会将每个 `名称：描述` 行写成独立 FAQ，重复行跳过，不影响其它新行写入。

## 5. 写库场景处理

典型纠错输入：

```text
添加知识库：
HiFleet 上绘制区域标注操作：...
key: <HIFLEET_KB_UPDATE_KEY>
```

Agent 应做：

1. 判断是否是明确写库指令。
2. 提取并结构化适用问题、标准答案、关键词、分类、意图和来源链接。
3. 调用 `upsert_local_kb_entry`，并确保 `raw_text` 保留完整正文与 `key: ...`。
4. 根据工具结果回复“已写入 / 重复未写入 / 缺信息 / 授权失败”。

Agent 不应做：

- 普通用户说“你答错了”就自动写库。
- 授权失败时继续写文件。
- 重复内容追加多条。
- 内容过短、来源不清、疑似重复或与已有知识冲突时直接写入；应先给出结构化预览并请用户确认或补充。
- 在客户回复里暴露 key、内部路径或原始 JSON。

## 6. 调试观察

平台操作类误答时，优先看：

- `generated_tool_calls`
- `route_trace.route`
- `route_trace.reasoning_trace.pipeline`
- `route_trace.reasoning_trace.understanding_result.user_goal`
- `route_trace.reasoning_trace.understanding_result.evidence_required`
- `route_trace.reasoning_trace.understanding_result.search_query_candidates`
- `route_trace.reasoning_trace.route_source`（`understanding_to_knowledge_chain` 表示由需求理解直接进入知识链）
- `retrieval_trace`
- `question_class`
- `web_answerability_reason`
- `risk_flags`
- `recommended_next_action`

如果是旧兼容 router 测试，还可看：

- `query_plan`
- `query_traces`
- `kb_answer_level`
- `browser_escalation_reason`
- `answer_completeness`

## 7. 回归命令

```bash
PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_router.py \
  tests/test_smart_search_tools.py \
  tests/test_knowledge_admin_tools.py
```

重点回归问题：

- `怎么绘制区域标注`
- `怎么添加电子围栏报警`
- `区域回放里怎么画临时区域`
- `标注不保存怎么办`
- 仅命中目录页/视频标题页时不得输出完整教程
- 授权写库成功、缺 key 拒绝、重复内容不追加
