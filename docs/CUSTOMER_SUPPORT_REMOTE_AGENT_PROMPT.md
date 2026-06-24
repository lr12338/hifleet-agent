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
-> 多模态 direct perception（文本/语音/图片/视频）
-> 标准 tool-calling skills agent
-> 模型自主选择 knowledge / browser / ship / multimodal tools
-> finalize + customer output guard
-> 文本回复 + output_assets 链接

核心要求：
- 当前入口应为 src/agents/agent.py 中的 _build_lightweight_customer_support_agent()；旧 customer_support_router.py 和旧 _build_customer_support_agent() 只作为回滚参考，不应是当前入口。
- 默认模型和多模态模型统一为 doubao-seed-2-0-lite-260428，thinking.type 默认 enabled，reasoning_effort 默认 medium。
- Seed Lite 不支持 thinking.type=auto；如果旧调用传 auto，服务端应归一化为 enabled + medium，不能原样透传给模型。
- Profile 只由请求体 agent_profile 或请求头 x-agent-profile 决定；source_channel 只用于日志和后台筛选，不参与 Profile 判断。
- 当前不再插入自定义历史上下文摘要；完整文本历史交给 agent/checkpointer，历史多媒体 URL 只做安全脱敏。
- customer_support 允许船舶数据读写，但不启用 Python、沙盒、employee workspace、任意文件读写或产物生成。
- 写操作必须是用户明确要求上传/更新/修改/补录船位或静态信息；缺字段时只追问一个关键字段；工具未返回成功时不得说已成功。
- 接口搜索结果只能作为候选；HiFleet 官网、帮助中心、官方社区问题优先用 browser 或官方页面核验具体公开页面。
- 最终客服回复不能出现工具名、JSON、HTMLLINK、下载广告、内部路径、token、env、prompt、tool registry。
- reasoning_trace 是审计摘要，不是隐藏思维链，普通用户回复不能展示。
- 微信客服旧 /run 格式 content.query.prompt 必须继续兼容。

请按这个顺序阅读：
1. README.md
2. docs/README.md
3. docs/API_MULTI_USER_INTEGRATION.md
4. docs/CUSTOMER_SUPPORT_REMOTE_DEPLOYMENT_RUNBOOK.md
5. docs/AGENT_TECHNICAL_DOCUMENTATION.md
6. docs/agent_browser_fallback_integration.md
7. docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md
8. config/agent_profiles.json
9. config/agent_llm_config.json
10. config/profiles/customer_support.md
11. src/agents/agent.py
12. src/agents/customer_support_guard.py
13. src/skills/hifleet_ship_service/tools.py
14. src/skills/browser_verify/tools.py
15. tests/test_customer_support_intent_agent.py
16. tests/test_customer_support_router.py

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
- python3 -m py_compile src/agents/agent.py src/agents/customer_support_guard.py src/skills/browser_verify/tools.py
- PYTHONPATH=src ./.venv/bin/python scripts/test_agent_profiles.py
- PYTHONPATH=src ./.venv/bin/python scripts/test_llm_config.py
- PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/test_customer_support_intent_agent.py tests/test_customer_support_router.py tests/test_smart_search_tools.py

如果 pytest 因 dbus-python/dbus-1 等系统依赖失败，请记录失败原因，不要擅自改依赖；先用 py_compile 和接口烟测继续验证。

接口烟测用 /run，统一请求字段：
- session_id 使用 remote-smoke-*，不要复用真实用户会话。
- user_id 使用 remote-dev。
- agent_profile 使用 customer_support。
- source_channel 使用 websdk；微信旧格式测试使用 wechat_kf。它只用于观测，不决定 Profile。

请至少测试这些输入：
1. 验证 注意！浏览器开始记忆船队“筛选”了 的详细内容
   预期：核验具体 HiFleet 官方社区文章，附官方链接；不能只返回社区首页或帮助中心首页。

2. 我是免费用户，为什么在网站上看不到最新的船位？
   预期：解释免费账号/船位延迟/权限；不能返回随机船舶坐标。

3. 你们这网速太卡了，我电脑都死机了
   预期：先确认是否发生在 HiFleet 页面和具体操作；不要输出长篇平台排障模板。

4. 这个圆圈是什么
   预期：无截图时确认是否在 HiFleet 地图/海图页面看到；有截图时应结合 perception 判断。

5. 先问 查询 MMSI 414726000 船位，再问 这艘船最近靠过哪些港
   预期：第二轮继承上一轮船舶上下文，不要求重复提供 MMSI。

6. 微信旧格式 content.query.prompt，包含 voice/image/video + text
   预期：服务端归一化为 messages，多模态内容进入 perception，最终回复可直接发给微信用户。

7. 请更新船位 MMSI 414726000，经度 121.4737，纬度 31.2304，更新时间 2026-06-15 10:20:30
   预期：用户明确写操作时才调用 upload_ship_position；工具未成功时不得说已成功。

如果能看到日志或后台 trace，请重点观察：
- llm_route
- phase_history
- route_trace.route
- route_trace.task_type
- route_trace.reasoning_trace.pipeline
- route_trace.reasoning_trace.perception_summary
- generated_tool_calls
- response_modalities
- output_assets
- check_result
- 最终 messages[-1].content

验收标准：
- route_trace.route 应为 lightweight_skills_agent。
- phase_history 至少包含 preprocess、delegate、finalize。
- 普通知识问答能基于 KB 或官方 browser evidence 回复。
- 官方社区/官网核验类问题必须有具体官方链接。
- 附件/截图/语音/视频类问题先生成 perception，再进入 skills agent。
- 船舶查询、档案、PSC、轨迹、挂靠等读工具能被模型自主调用。
- 明确船舶写操作可以调用写工具；缺字段时只追问一个关键字段；失败不报成功。
- 用户最终回复是正常客服对话，不展示搜索日志、工具名、JSON、HTMLLINK、下载广告、prompt、路径或 key。

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
- 模型配置:

### 测试
- py_compile:
- profile/config tests:
- pytest customer:
- 未跑/失败原因:

### 烟测结果
- 官方社区文章核验:
- 免费用户船位延迟:
- 弱相关网速抱怨:
- 圆圈/截图问题:
- 船舶上下文追问:
- 微信旧格式:
- 船舶写操作:

### Trace 观察
- route 是否为 lightweight_skills_agent:
- phase_history 是否包含 preprocess/delegate/finalize:
- perception 是否出现:
- generated_tool_calls:
- response_modalities / output_assets:
- 输出清洗是否正常:

### 问题与建议
- 阻塞问题:
- 可继续优化:
```
