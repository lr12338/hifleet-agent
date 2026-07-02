# 知识检索链指南

本文只描述当前代码里真实生效的知识检索链。当前设计不是“拆多个 skill”，而是：

- `knowledge_qa` 保持为一个 skill
- tool 层拆成三个主工具
- `smart_search` 仅作为兼容 facade 保留

实现文件：

- `src/skills/knowledge_qa/tools.py`
- `src/skills/knowledge_qa/local_kb_runtime.py`
- `src/skills/knowledge_qa/web_search_runtime.py`
- `src/skills/knowledge_qa/browser_bridge.py`
- `config/system_prompt_base.md`
- `config/profiles/customer_support.md`

## 1. 当前主链

`customer_support` 收到知识问题后，先由需求理解 Agent 输出 `knowledge` 路由、检索关键词和站点偏好，再由 planner/knowledge 工具链受控调用 `knowledge_qa` 工具。

```mermaid
flowchart TD
    Q[知识问题] --> U[需求理解 Agent]
    U --> Plan[生成 3-5 组关键词与检索偏好]
    Plan --> A[local_kb_search]
    A -->|FAQ 强命中| Review[证据充分性复核]
    A -->|不足| B[web_search]
    B -->|命中具体事实页| Done
    B -->|只有候选页线索| C[web_search_agent_browser]
    C --> D[bridge 到 browser_verify]
    D --> Review
    Review --> Done[回答或保守收口]
```

固定升级顺序：

1. `local_kb_search`
2. `web_search`
3. `web_search_agent_browser`

约束：

- tool 只做本层事情，不在内部偷偷串下一层
- customer_support planner 或 `smart_search` facade 负责编排
- browser 不是第一轮搜索工具；但在 `web_search` 弱命中后，可用短关键词通过 Bing 优先召回 HiFleet 官方候选页
- 平台操作和问题反馈类问题要覆盖多个证据面，不应只用单个 query 或单条摘要收口

## 2. 模型主导检索如何影响查询

当前默认使用 `doubao-seed-2-0-lite-260428`、`thinking_type=enabled` 和 `reasoning_effort=high`。需求理解 Agent 会根据当前 `agent_profile` prompt、当前轮多模态摘要和会话记忆选择查询词、是否先查本地知识库、是否升级 web/browser。

知识检索最关心的语义字段是：

- `rewritten_user_need`
- `query_type`
- `search_keywords`
- `search_query_candidates`
- `should_prefer_local_kb`
- `should_limit_to_hifleet_sites`

影响方式：

- 当前 skills agent 会把用户问题、多模态摘要和必要上下文一起传给 knowledge 工具
- 历史 router 的知识链仅作为回滚与兼容测试入口保留，新逻辑优先落在 skills agent 和三工具实现里
- 不再默认套旧的 `_rewrite_hifleet_knowledge_query()` 模板
- browser 的 `site_hint` 优先使用 `should_limit_to_hifleet_sites`
- 当前 trace 优先观察 `generated_tool_calls`、`route_trace.reasoning_trace.pipeline`、`perception_summary` 和 `retrieval_trace`

平台操作类或问题反馈类 query 要求模型主动生成多组关键词，例如“怎么绘制区域标注”应覆盖：

- `HiFleet 区域标注 绘制 步骤`
- `HiFleet 电子围栏 标注及电子围栏报警`
- `HiFleet 我的标注 区域标注 编辑 报警`
- `HiFleet 区域回放 绘制 区域`
- `HiFleet 多边形 圆形 矩形 区域标注`

最终回答前必须判断证据是否覆盖入口、动作、保存/完成条件；如果只确认“支持该功能”，不能拼成完整教程。

示例：

- `Hifleet筛选船队有记忆功能吗`
  - 主查询应接近 `hifleet 筛选船队 记忆功能`
- `今日长江水位`
  - 主查询应接近 `今日长江水位 长江海事局 交通运输部`
  - 不应误带 HiFleet 站点过滤

## 3. `knowledge_qa` 三工具

### 3.1 `local_kb_search`

第一版直接检索仓库内 `docs/RAG`，不依赖远端 KB recall。

数据源优先级：

- `docs/RAG/hifleet_cs_outputs/客服知识库结构化.jsonl`
- `docs/RAG/hifleet_cs_outputs/客服问答对.md`
- `docs/RAG/hifleet_cs_outputs/FAQ检索词.md`
- `docs/RAG/hifleet_cs_wiki/*.md`
- `docs/RAG/raw/产品文档/*.md`

当前行为：

- FAQ 强命中：`can_answer=true`
- 只有 wiki / 产品文档主题说明：`can_answer=false`，`should_continue=true`
- 完全 miss：`can_answer=false`，`should_continue=true`

返回重点：

- `items[].source_type`
- `items[].score`
- `trace.source_breakdown`

### 3.2 `web_search`

这是三工具里最核心的一层，负责“关键词式结构化联网搜索 + 结果分析”。

优先调用结构化搜索接口：

```text
POST https://open.feedcoopapi.com/search_api/web_search
Authorization: Bearer <API_KEY>
```

默认请求特征：

- `SearchType=web`
- `NeedSummary=true`
- `NeedUrl=true`
- `NeedContent=false`
- `ContentFormats=text`
- `QueryRewrite=false`

query 规则：

- 保留 2 到 5 个高信息量词块
- 产品问题优先：品牌词 + 功能词 + 判定词
- 公共数据问题优先：主题词 + 时效词 + 机构词
- 禁止自动补“产品功能 使用说明”这类泛化尾词

请求画像分三类：

- `hifleet_product`
- `authoritative_public_data`
- `general_public_info`

站点过滤规则：

- 只有 `hifleet_product` 允许 `Sites=HIFLEET_SITES`
- `authoritative_public_data` 禁止带 `Sites`
- `general_public_info` 默认不带 `Sites`

返回分析会补这些标签：

- `is_hifleet_official`
- `is_authoritative`
- `is_specific_page`
- `is_directory_page`
- `is_aggregated_page`
- `has_specific_fact`
- `question_class`
- `web_answerability_reason`
- `risk_flags`

结果评估规则：

- 具体官方页且摘要含明确事实：`can_answer=true`
- 平台教程/问题反馈类需要更严格：必须是具体官方页或正文型页面，且包含点击、选择、填写、保存、进入、关闭、设置等动作证据
- 权威公共页且摘要含日期/数值/机构信息：`can_answer=true`
- 只有目录页或聚合页：`should_continue=true`
- 有具体候选页但摘要不足：`continue_with=agent_browser`
- 若公共数据 query 却携带 `Sites`：标记 `risk_flags=["site_filter_polluted"]`

### 3.3 Ark fallback

只有结构化搜索失败时才允许退回 Ark 生成式联网搜索。

要求：

- 必须保留 `used_ark_fallback=true`
- 必须保留原始 `request_profile`
- 不允许 fallback 覆盖原始 query 类型判断或站点过滤策略

### 3.4 `web_search_agent_browser`

这个工具不做第一轮开放式搜索，主要负责“目标页已锁定后抓正文”。当 `web_search` 无有效命中且问题仍可能属于 HiFleet 平台/产品/社区/帮助内容时，也允许用短关键词触发 Bing 官方候选召回。

它只是 `knowledge_qa` 对 `browser_verify.agent_browser_deep_search` 的包装桥接：

- 负责把 `target_urls / site_hint / query` 整形成 browser 输入
- 负责把 browser 输出适配成 `knowledge_qa` 统一协议
- browser 失败时返回结构化失败，不伪装成功

严格限制：

- 无 `target_urls` 时必须来自 `web_search` 弱命中后的最后核验，且 `query` 必须是短关键词串
- 候选排序始终优先 HiFleet 官方社区、官网、帮助中心；无官方正文证据时保守失败
- 不允许把首页、社区目录页、帮助中心入口页当成成功证据

## 4. `smart_search` 当前定位

`smart_search` 仍保留，但已经不是推荐的新主入口。

它现在的角色是兼容 facade：

- 兼容旧 prompt
- 兼容旧 route
- 兼容旧测试

内部会尽量复用：

- `local_kb_search`
- `web_search`
- 必要时的 browser bridge

原则：

- 新逻辑优先写在三工具实现里
- 不要再把新能力只堆回 `smart_search`

## 5. 统一输出协议

三个主工具都返回 JSON 字符串，字段尽量统一：

- `tool`
- `query`
- `status`
- `can_answer`
- `should_continue`
- `continue_with`
- `confidence`
- `summary`
- `items`
- `best_urls`
- `recommended_next_action`
- `trace`

这样 agent 可以直接基于结构化字段决策，而不是从大段自然语言里反推下一步。

## 6. 链接规范

统一帮助中心：

```text
https://www.hifleet.com/helpcenter/?i18n=zh
```

规则：

- 不允许编造 URL
- 不允许输出占位链接
- 候选链接可访问性校验失败时，应剔除或降级到官方帮助中心

## 7. Linux 部署配置

当前项目启动入口 [src/main.py](../src/main.py) 会在启动时加载：

```text
COZE_WORKSPACE_PATH/.env
```

Linux 服务器部署时需要确认：

1. `COZE_WORKSPACE_PATH` 指向实际工作目录
2. 对应目录下存在 `.env`
3. 进程启动用户对 `.env` 有读取权限

结构化联网搜索当前兼容这些环境变量名：

```bash
VOLC_WEB_SEARCH_API_KEY=
WEB_SEARCH_API_KEY=
TORCHLIGHT_API_KEY=
ARK_WEBSEARCH_API_KEY=
ark_websearch_api_key=
```

现网如果已经使用 `ark_websearch_api_key`，无需修改变量名即可命中结构化联网搜索逻辑。

## 8. 常用排障

### 8.1 公共数据 query 被 HiFleet 站点污染

示例：`今日长江水位`

期望：

- understanding 输出 `query_type=authoritative_public_data`
- `search_query_candidates[0]` 接近 `今日长江水位 长江海事局 交通运输部`
- `web_search.request_profile.Filter.Sites` 为空

若仍带 `Sites`，优先检查：

- skills agent 传给 `web_search` 的 query 是否已经被 profile prompt 错误限制到 HiFleet 站点
- `web_search_runtime.looks_like_authoritative_data_query(...)`
- fallback trace 是否覆盖了真实请求画像

### 8.2 平台操作类问题过早收口

示例：`怎么绘制区域标注`

期望：

- 优先命中结构化 FAQ，例如 `客服知识库结构化.jsonl` 中的区域标注步骤。
- 至少生成 3 组相关关键词，覆盖区域标注、电子围栏、我的标注、区域回放等角度。
- 仅命中帮助中心首页、社区目录、视频标题页时，`web_search.can_answer=false`。
- 最终教程答案必须包含入口、操作动作、保存/完成条件。

若仍输出未经核验的完整步骤，优先检查：

- `config/profiles/customer_support.md` 的多关键词和证据充分性规则是否被删改。
- `web_search_runtime.analyze_web_search_result(...)` 是否把目录页或视频标题页判成可答。
- 本地 FAQ 是否缺少结构化步骤答案。

### 8.3 平台问题误入船舶链路

示例：`HiFleet 轨迹加载失败怎么办`

期望：

- `customer_support` 下 `route_trace.route=knowledge`
- `customer_ceshi` 下可进入 knowledge 或内部测试链路
- knowledge 检索应优先围绕平台问题本身，不误调船舶读写工具

若误入船舶链路，检查：

- `config/profiles/customer_support.md` 中船舶写操作和平台知识边界是否被近期修改
- 会话上下文是否错误继承了上一个船舶实体

### 8.4 搜索太慢

检查：

- 是否简单问题误升级到 browser
- 是否链接校验数量过大
- 是否多轮中重复搜索相同问题，缓存 TTL 是否生效

相关环境变量：

```bash
SMART_SEARCH_CACHE_TTL_SEC=600
SMART_SEARCH_URL_TIMEOUT_SEC=2.0
SMART_SEARCH_URL_TOP_N=2
SMART_SEARCH_DEEP_VARIANTS_MAX=3
VOLC_WEB_SEARCH_TIMEOUT_SEC=15
VOLC_WEB_SEARCH_DEFAULT_COUNT=5
```

### 8.5 结构化联网搜索未生效

现象：

- 日志里频繁出现 `Structured web search failed, fallback to Ark`
- `used_ark_fallback=true`
- 结果明显退化成“从生成文本里抽链接”

检查顺序：

1. 进程是否加载了正确的 `.env`
2. `ark_websearch_api_key` 或其他兼容变量名是否已注入进程环境
3. Linux 服务器是否能访问 `https://open.feedcoopapi.com/search_api/web_search`
4. 是否触发接口错误码：
   - `10403` 权限错误
   - `10406` 免费额度用尽
   - `700429` QPS 限流

### 8.6 `.venv-test` 下历史 router 测试导入失败

当前已知环境阻塞：

- `.venv-test` 缺少 `docker` Python 包
- 导致 `browser_verify` skill 导入失败
- 这会影响历史兼容测试 `tests/test_customer_support_router.py`，不是当前轻量 customer 主链本身引入的问题

可先做的最小验证：

- `python3 -m py_compile`
- `tests/test_customer_support_intent_agent.py`
- `tests/test_smart_search_tools.py`

### 8.7 官网链接不可访问

处理：

1. 确认是否是 `help.hifleet.com` 历史链接
2. 优先替换为统一帮助中心
3. 不确定时不要输出该链接

## 9. 回归验证

知识链路由客服回归覆盖：

```bash
.venv/bin/python scripts/hifleet_agent_regression.py
```

重点场景：

- `knowledge_glossary_fast`
- `智能视频监控`
- `今日长江水位`
- `Hifleet筛选船队有记忆功能吗`
- 平台故障类问题不误入船舶链路
- 输出链接可访问或降级到官方帮助中心

## 10. 维护知识内容

新增或更新 FAQ 后：

1. 优先补 `docs/RAG` 对应 FAQ / wiki / 产品文档
2. 用典型用户问法验证 `local_kb_search`
3. 再验证 `web_search` 的 query 和 `Sites` 是否符合预期
4. 跑客服回归，确认没有触发不必要 browser 升级

授权在线写库由 `knowledge_admin.upsert_local_kb_entry` 处理。使用前必须设置 `HIFLEET_KB_UPDATE_KEY`，调用时只能通过正文 `key: ...` 传入；`x-kb-update-key` header 不再支持。工具会写入结构化 JSONL、去重并刷新本地 KB 缓存；普通纠错或闲聊不应触发写库。
