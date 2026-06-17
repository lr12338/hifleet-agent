# Customer Support 异地服务器部署联调手册

本文面向把当前项目部署到其他 Linux 服务器上测试的开发人员，目标是帮助大家快速确认：

- 服务是否按当前代码真实启动
- `customer_support` 主链是否走到了最新实现
- 轻量多模态识别、轻量意图 Agent、`agent-browser`、上下文压缩、knowledge/harness 是否在远端生效

## 0. 当前客服主链快照

当前 `customer_support` 远端联调时，优先按下面这条链路理解问题：

```text
前置安全检查
-> 附件/截图轻量 perception
-> 轻量 intent agent 输出 route decision
-> 少量 deterministic guard 修正高风险场景
-> execute/planner/harness 或 delegate
-> check/finalize 输出清洗
```

关键点：

- 截图、文件、音视频等附件会先被轻量模型或启发式逻辑整理成 `perception_result`，再参与路由判断。
- 平台知识、图标含义、页面报错、社区文章核验等普通客服问题，优先由轻量 intent agent 判断，不再主要依赖关键词硬分流。
- 安全拒答、写操作保护、明确船舶工具调用、文件解析等仍有 deterministic guard 兜底。
- `agent_browser_deep_search` 只用于 HiFleet 官网、帮助中心、官方社区等公开页面核验，接口搜索结果不能单独支撑确定结论。

## 1. 先确认版本

在远端机器先确认部署代码版本，不要直接假设和本地一致。

建议至少确认：

```bash
git rev-parse HEAD
git status --short
```

如果远端不是当前分支的同一提交，先不要用现象倒推代码。

## 2. 启动前环境检查

### 2.1 Python 与依赖

```bash
python3 --version
pip show fastapi langgraph langchain-openai requests
```

### 2.2 关键环境变量

至少确认这些变量存在：

```bash
COZE_WORKLOAD_IDENTITY_API_KEY
COZE_INTEGRATION_MODEL_BASE_URL
COZE_INTEGRATION_BASE_URL
PGDATABASE_URL
ADMIN_API_KEY
SHIP_SERVICE_API_URL
SHIP_SERVICE_API_TOKEN
```

如果要测试截图/附件识别，还要确认 `config/agent_llm_config.json` 中配置了可用的 `multimodal_model`，并且远端能访问对应模型服务。

### 2.3 agent-browser 能力

```bash
which agent-browser
agent-browser --help
```

如果这里失败，`agent_browser_deep_search` 在远端一定不会生效。

## 3. 启动后健康检查

```bash
curl http://127.0.0.1:10123/health
```

如果部署带 systemd，建议同时看：

```bash
sudo systemctl status hifleet-agent.service --no-pager
sudo journalctl -u hifleet-agent.service -n 200 --no-pager
```

## 4. 先做最小回归

建议先跑这两组测试：

```bash
./.venv-test/bin/python -m pytest tests/test_customer_support_router.py -q
./.venv-test/bin/python -m pytest tests/test_customer_support_intent_agent.py -q
```

这两组能最快验证：

- 当前 route/execute/knowledge/browser 链是否正常
- 轻量 perception + intent agent 是否参与路由
- 上下文压缩是否生效
- intent/planner 是否使用压缩后的相关历史

如果远端 Python 环境暂时无法完整跑 pytest，至少先做语法级检查：

```bash
python3 -m py_compile \
  src/agents/agent.py \
  src/agents/customer_support_router.py \
  src/agents/customer_support_guard.py \
  src/skills/browser_verify/tools.py
```

如果 pytest 在安装依赖时卡在 `dbus-python` / `dbus-1`，通常是服务器缺少系统级 DBus 开发包。优先使用项目已有虚拟环境；需要新建环境时，先在服务器上补齐系统依赖，再重跑测试。

## 5. 远端接口联调建议

### 5.1 测 knowledge + browser fallback

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "HiFleet 帮助中心英文版入口在哪"}],
    "session_id": "remote-smoke-knowledge-001",
    "user_id": "remote-dev",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

看点：

- 是否得到 HiFleet 相关结论
- 返回文本是否客服化
- 是否没有内部路径、工具名、命令

### 5.2 测官方社区具体文章核验

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "验证 注意！浏览器开始记忆船队“筛选”了 的详细内容"}],
    "session_id": "remote-smoke-browser-article-001",
    "user_id": "remote-dev",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

预期：

- 找到并概括 HiFleet 官方社区具体文章，不只返回社区首页或帮助中心首页。
- 最终回复包含可参考的官方链接。
- 最终回复不出现 `综合摘要`、`查询1`、`HTMLLINK`、`agent_browser`、`下载APP` 等内部或广告化文本。

### 5.3 测免费用户船位延迟解释

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "我是免费用户，为什么在网站上看不到最新的船位？"}],
    "session_id": "remote-smoke-free-position-001",
    "user_id": "remote-dev",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

预期：

- 按 HiFleet 免费账号、船位延迟、权限或刷新机制解释。
- 不应调用随机船舶查询，也不应返回某条无关船舶的 MMSI、坐标和航速。

### 5.4 测弱相关抱怨不过拟合

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "你们这网速太卡了，我电脑都死机了"}],
    "session_id": "remote-smoke-complaint-001",
    "user_id": "remote-dev",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

预期：

- 先轻量确认是否发生在 HiFleet 页面，以及具体页面/操作。
- 不直接输出长篇平台排障模板。

### 5.5 测多轮上下文压缩

先发第一轮：

```json
{
  "messages": [{"role": "user", "content": "hifleet平台上传不了航线怎么办"}],
  "session_id": "remote-context-001",
  "user_id": "remote-dev",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

再发第二轮：

```json
{
  "messages": [{"role": "user", "content": "今天上海天气怎么样"}],
  "session_id": "remote-context-001",
  "user_id": "remote-dev",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

预期：

- 第二轮不应被第一轮“上传航线失败”误导
- 不应继续沿着平台排障回答

### 5.6 测上下文追问仍可用

第一轮：

```json
{
  "messages": [{"role": "user", "content": "查询 MMSI 414726000 船位"}],
  "session_id": "remote-ship-followup-001",
  "user_id": "remote-dev",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

第二轮：

```json
{
  "messages": [{"role": "user", "content": "这艘船最近靠过哪些港"}],
  "session_id": "remote-ship-followup-001",
  "user_id": "remote-dev",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

预期：

- 第二轮可以复用上一轮船舶上下文
- 不需要重复提供 MMSI

### 5.7 测截图/附件优先路由

通过调用方真实附件字段发送一张 HiFleet 海图或页面截图，并配文：

```text
这个圆圈是什么
```

预期：

- `perception_result` 能摘要截图内容、可见文字、疑似符号或页面问题。
- `route_trace.reasoning_trace.route_source` 优先为 `light_agent` 或 guard 修正后的来源。
- 如果截图像 HiFleet 海图，应进入 `chart_symbol` 或 `platform_knowledge`，而不是普通闲聊。
- 如果截图不清晰，回复应只追问一个关键补充，例如“请补一张更清晰的截图，最好圈出要确认的位置。”

## 6. 远端重点观察字段

如果你通过后台或日志查看运行态，优先看：

- `phase_history`
- `route_trace.route`
- `route_trace.task_type`
- `route_trace.fallback_reason`
- `route_trace.reasoning_trace`
- `generated_tool_calls`
- `check_result`

### 6.1 你想看到什么

- `phase_history` 包含 `executed` 或 `delegated`
- 知识链弱命中时，`generated_tool_calls` 里可看到 `agent_browser_deep_search`
- `fallback_reason` 可能出现 `smart_search_empty_agent_browser_fallback`
- `reasoning_trace.route_source` 能区分 `light_agent`、`safety_rule`、`write_guard`、`fallback_rule`
- `reasoning_trace.perception_summary` 能说明截图/文件识别结果
- `reasoning_trace.intent_agent_result` 能看到轻量 Agent 的结构化路由判断
- `reasoning_trace.tool_summary.official_source_count` 能反映官方来源数量
- `check_result.evidence_summary` 能看到最终答案依据的证据摘要

### 6.2 你不想看到什么

- 最终回复里出现 `smart_search`
- 最终回复里出现 `agent-browser`
- 最终回复里出现 `reasoning_trace` 或 JSON
- 最终回复里出现路径、命令、`.env`、`token`

## 7. 常见远端问题

### 7.1 本地通过，远端不触发 agent-browser

优先排查：

1. `agent-browser` 是否安装
2. 服务器是否能访问 Bing
3. 服务器是否能访问 `hifleet.com`
4. 远端代码是否还是旧版

### 7.2 多轮上下文表现和本地不一致

优先排查：

1. 远端是否启用了不同的 checkpointer
2. 多 worker 下是否复用了同一 `session_id`
3. 是否还有旧服务进程未重启

### 7.3 日志里能看到工具调用，但客户回复不对

优先排查：

1. `sanitize_customer_output(...)` 是否触发了兜底
2. `check_result.links_ok` 是否失败
3. planner/harness 结果是否被 delegate 覆盖
4. `check_result.evidence_summary` 是否缺少官方页面证据
5. `route_trace.reasoning_trace.route_source` 是否被 fallback rule 接管

### 7.4 截图问题没有按截图内容路由

优先排查：

1. 请求体是否真的把附件 metadata 传到 Agent。
2. `perception_result` 是否为空。
3. `multimodal_model` 是否配置且远端可访问。
4. 轻量 intent agent 是否返回低置信并回退旧规则。

## 8. 开发同学建议阅读顺序

1. [docs/CUSTOMER_SUPPORT_REMOTE_AGENT_PROMPT.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/CUSTOMER_SUPPORT_REMOTE_AGENT_PROMPT.md)
2. [docs/AGENT_TECHNICAL_DOCUMENTATION.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/AGENT_TECHNICAL_DOCUMENTATION.md)
3. [docs/agent_browser_fallback_integration.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/agent_browser_fallback_integration.md)
4. [docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md)
5. [config/profiles/customer_support.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/config/profiles/customer_support.md)
6. [src/agents/agent.py](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/src/agents/agent.py)
7. [src/agents/customer_support_router.py](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/src/agents/customer_support_router.py)
8. [src/skills/browser_verify/tools.py](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/src/skills/browser_verify/tools.py)
