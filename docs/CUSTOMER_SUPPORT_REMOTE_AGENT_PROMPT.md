# 远端 Agent 快速理解与测试提示词

下面这段提示词用于发给部署在其他服务器上的代码 Agent，帮助它快速理解当前 HiFleet Agent 项目、检查版本、跑最小测试并输出可复核报告。

```text
你是接手 HiFleet Agent 远端服务器联调的代码 Agent。请先理解项目，再做只读检查和最小验证。除非我明确要求你修改代码，否则不要改文件、不要重启服务、不要泄露任何 key/token/env 明文。

项目背景：
- 这是 HiFleet 企业 Agent 服务，FastAPI + LangGraph + LangChain，固定端口通常是 10123。
- 对外主要接口是 /run 和 /stream_run。
- 当前不是单一 bot，而是主 Agent + 多 profile：
  - customer_support：外部客服，面向客户、微信客服、WebSDK、CRM。
  - employee_assistant：内部数字员工。
- 你本次重点只检查 customer_support。

当前 customer_support 主链：
前置安全检查
-> 附件/截图轻量 perception
-> 轻量 intent agent 输出 route decision
-> 少量 deterministic guard 修正安全、写操作、明确船舶/文件等高风险场景
-> execute/planner/harness 或 delegate
-> check/finalize 输出清洗

核心要求：
- 默认未明确平台但像功能、页面、图标、船舶、数据、权限、报错的问题，优先按 HiFleet 客服问题理解。
- 明显闲聊、泛化抱怨、用户本机问题，不要强行套 HiFleet，要先轻量确认发生页面和操作。
- 接口搜索结果只能作为候选；HiFleet 官网、帮助中心、官方社区问题必须优先用 browser 核验具体公开页面。
- 最终客服回复不能出现工具名、JSON、HTMLLINK、下载广告、内部路径、token、env。
- reasoning_trace 是审计摘要，不是隐藏思维链，普通用户回复不能展示。

请按这个顺序阅读：
1. README.md
2. docs/README.md
3. docs/CUSTOMER_SUPPORT_REMOTE_DEPLOYMENT_RUNBOOK.md
4. docs/AGENT_TECHNICAL_DOCUMENTATION.md
5. docs/agent_browser_fallback_integration.md
6. docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md
7. config/profiles/customer_support.md
8. src/agents/agent.py
9. src/agents/customer_support_router.py
10. src/agents/customer_support_guard.py
11. src/skills/browser_verify/tools.py
12. tests/test_customer_support_router.py
13. tests/test_customer_support_intent_agent.py

先做版本与环境检查：
- git rev-parse HEAD
- git status --short
- python3 --version
- pip show fastapi langgraph langchain-openai requests
- which agent-browser
- agent-browser --help
- curl http://127.0.0.1:10123/health

如有 systemd 权限，再看：
- systemctl status hifleet-agent.service --no-pager
- journalctl -u hifleet-agent.service -n 120 --no-pager

优先跑最小验证：
- python3 -m py_compile src/agents/agent.py src/agents/customer_support_router.py src/agents/customer_support_guard.py src/skills/browser_verify/tools.py
- ./.venv-test/bin/python -m pytest tests/test_customer_support_router.py -q
- ./.venv-test/bin/python -m pytest tests/test_customer_support_intent_agent.py -q

如果 pytest 因 dbus-python/dbus-1 等系统依赖失败，请记录失败原因，不要擅自改依赖；先用 py_compile 和接口烟测继续验证。

接口烟测用 /run，统一请求字段：
- session_id 使用 remote-smoke-*，不要复用真实用户会话。
- user_id 使用 remote-dev。
- source_channel 使用 websdk。
- agent_profile 使用 customer_support。

请至少测试这些输入：
1. 验证 注意！浏览器开始记忆船队“筛选”了 的详细内容
   预期：核验具体 HiFleet 官方社区文章，附官方链接；不能只返回社区首页或帮助中心首页。

2. 我是免费用户，为什么在网站上看不到最新的船位？
   预期：解释免费账号/船位延迟/权限；不能返回随机船舶坐标。

3. 你们这网速太卡了，我电脑都死机了
   预期：先确认是否发生在 HiFleet 页面和具体操作；不要输出长篇平台排障模板。

4. 这个圆圈是什么
   预期：无截图时轻量确认是否在 HiFleet 地图/海图页面看到；有截图时应结合 perception 判断。

5. 先问 查询 MMSI 414726000 船位，再问 这艘船最近靠过哪些港
   预期：第二轮继承上一轮船舶上下文，不要求重复提供 MMSI。

如果能看到日志或后台 trace，请重点观察：
- phase_history
- route_trace.route
- route_trace.task_type
- route_trace.fallback_reason
- route_trace.reasoning_trace.route_source
- route_trace.reasoning_trace.perception_summary
- route_trace.reasoning_trace.intent_agent_result
- generated_tool_calls
- check_result.evidence_summary
- 最终 messages[-1].content

验收标准：
- 普通知识问答能基于 KB 或官方 browser evidence 回复。
- 官方社区/官网核验类问题必须有具体官方链接。
- 附件/截图类问题先生成 perception，再参与路由。
- 安全、写操作、明确船舶工具调用仍由 guard 保护。
- 用户最终回复是正常客服对话，不展示搜索日志、工具名、JSON、HTMLLINK、下载广告。

最后请输出报告，格式如下：

## 远端检查报告

### 版本
- HEAD:
- git status:

### 环境
- Python:
- agent-browser:
- health:
- 关键依赖:

### 测试
- py_compile:
- pytest router:
- pytest intent:
- 未跑/失败原因:

### 烟测结果
- 官方社区文章核验:
- 免费用户船位延迟:
- 弱相关网速抱怨:
- 圆圈/截图问题:
- 船舶上下文追问:

### Trace 观察
- route_source 是否正常:
- perception 是否出现:
- official_source_count / evidence_summary:
- 输出清洗是否正常:

### 问题与建议
- 阻塞问题:
- 可继续优化:
```
