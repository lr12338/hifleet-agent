# Skill: knowledge-qa

## 概述
知识检索与问答域，处理所有信息查询请求。优先使用三工具串联：

1. `local_kb_search`
2. `web_search`
3. `web_search_agent_browser`

仅为兼容旧链路保留 `smart_search`，新调用优先使用三工具，不要把全部流程压回单工具里。

## 适用场景
- HiFleet 平台功能咨询、教程、权限、帮助中心问题
- 航运行业知识、公开权威数据、实时动态
- 产品功能介绍、社区文章检索、长尾问题核验

## 工具

### local_kb_search
- 优先检索本地 `docs/RAG`
- 命中 FAQ/标准回复时优先停止
- 返回结构化 JSON，关注：
  - `can_answer`
  - `should_continue`
  - `items`
  - `trace`

### web_search
- 负责关键词式结构化联网搜索
- query 应尽量是关键词串，不是长问句模板
- 只有明确 HiFleet 产品/功能问题才允许带 `Sites=hifleet...`
- 行业权威数据问题不要带 HiFleet 站点过滤
- 返回结构化 JSON，重点读取：
  - `request_profile`
  - `result_profile`
  - `items`
  - `best_urls`
  - `trace.risk_flags`

### web_search_agent_browser
- 仅作为最后一轮公开页面核验，不作为第一轮搜索工具
- 有明确页面时传 `target_urls`，用于抓取具体页面正文
- 当 `web_search` 无有效命中、`can_answer=false` 或候选摘要不足，且问题仍可能属于 HiFleet 平台/产品/社区/帮助内容时，可以不传 `target_urls`，只传关键词 `query`
- 无 `target_urls` 时，`query` 必须是短关键词串；browser 会通过 Bing 优先寻找 HiFleet 官方社区、官网、帮助中心候选页
- 若无正文、仅目录页、或抓取失败，不要伪装成成功答案

### smart_search
- 兼容旧入口
- 内部会按 `local_kb_search -> web_search` 的思路编排
- 新 prompt 不应优先依赖它

## 使用规则

1. 先调用 `local_kb_search`
2. 普通问题若 `can_answer=true`，可直接基于知识库回答；`evidence_required=true` 时不得因此提前结束
3. 若 `should_continue=true`，调用 `web_search`
4. 普通问题若 `web_search.can_answer=true`，可直接回答；`evidence_required=true` 时继续完成页面核验
5. `web_search.continue_with=agent_browser` 或强证据链需要核验时：有 `best_urls` 则传入 `target_urls`；没有 URL 时传空 `target_urls` 与短关键词，让 browser 通过 Bing 寻找 HiFleet 官方候选页面
6. browser 只有抓到官方、具体、相关且有事实/步骤证据的正文时才可 `can_answer=true`；目录、首页、空正文、超时和无关页必须继续后续 query 或保守收口
7. 默认最多执行 3 组高质量 query，避免重复检索；最终回答时优先引用本地知识库或官方页面

## 输出要求

- 不向用户暴露工具名、trace、payload、内部搜索过程
- 不输出 `AI摘要`、`回答指导`、`[Query1:...]` 这类模板噪音
- 优先保留：明确结论、官方来源、可访问链接
- 当证据不足时，保守回答并说明仍需核验
