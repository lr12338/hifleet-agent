# Customer Support 异地服务器部署联调手册

本文面向把当前项目部署到其他 Linux 服务器上测试的开发人员，目标是快速确认：

- 服务是否按当前代码真实启动。
- `customer_support` 是否走到需求理解 Agent 主导链路，而不是历史轻量 delegate 链。
- 微信客服旧 `/run` 调用、多模态预处理、船舶读写工具、knowledge/browser fallback 和会话记忆是否在远端生效。

## 0. 当前客服主链快照

当前 `customer_support` 主链按下面这条链路理解：

```text
前置安全检查 / 多模态 direct perception（文本/语音/图片/视频）
-> 需求理解 Agent 输出 intent/route/参数组/缺失项
-> 安全兜底与写操作保护
-> harness 或 planner 调用受控工具
-> 结果分析 / 输出清洗
-> 文本回复 + output_assets 链接
```

关键点：

- 默认文本模型和多模态模型统一为 `doubao-seed-2-0-lite-260428`，`thinking_type=enabled`，`reasoning_effort=high`。
- 当前入口是 `src/agents/agent.py` 中的 `_build_customer_support_agent()`；`_build_lightweight_customer_support_agent()` 仅保留为历史/回滚参考。
- Profile 只由请求体 `agent_profile` 或请求头 `x-agent-profile` 决定；`source_channel` 只用于日志和后台筛选。
- 当前不再插入自定义历史上下文摘要；完整文本历史交给 agent/checkpointer，历史多媒体 URL 只做安全脱敏。
- `customer_support` 允许调用 HiFleet 船舶读写工具，但不启用 Python、沙盒、employee workspace、任意文件读写或产物生成。
- `agent_browser_deep_search` 只用于公开网页核验；最终客户回复不得暴露工具名、JSON、prompt、路径、日志或 key/token。

## 1. 先确认版本

在远端机器先确认部署代码版本，不要直接假设和本地一致。

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
ark_websearch_api_key
```

推荐模型配置：

```json
{
  "text_model": "doubao-seed-2-0-lite-260428",
  "multimodal_model": "doubao-seed-2-0-lite-260428",
  "thinking_type": "enabled",
  "reasoning_effort": "high"
}
```

### 2.3 agent-browser 能力

```bash
which agent-browser
agent-browser --help
```

如果这里失败，`agent_browser_deep_search` 在远端不会生效；本地知识库和普通 web search 仍可继续测试。

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

建议先跑：

```bash
PYTHONPATH=src ./.venv/bin/python scripts/test_agent_profiles.py
PYTHONPATH=src ./.venv/bin/python scripts/test_llm_config.py
PYTHONPATH=src ./.venv/bin/python -m pytest -q \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_router.py \
  tests/test_hifleet_ship_upload_position.py \
  tests/test_hifleet_ship_static_update.py \
  tests/test_smart_search_tools.py
```

这些测试会覆盖：

- `customer_support` 工具列表包含船舶读写工具，且不包含 sandbox / Python / employee workspace 工具。
- 当前 `build_agent()` 已把 `customer_support` 路由到需求理解主导 graph。
- 文本、语音、图片/视频感知摘要能进入需求理解。
- 船位更新和静态信息更新会经过参数组抽取、写操作保护和工具硬校验。

如果远端 Python 环境暂时无法完整跑 pytest，至少先做语法级检查：

```bash
python3 -m py_compile \
  src/agents/agent.py \
  src/agents/customer_support_guard.py \
  src/skills/browser_verify/tools.py
```

如果 pytest 在安装依赖时卡在 `dbus-python` / `dbus-1`，通常是服务器缺少系统级 DBus 开发包。优先使用项目已有虚拟环境；需要新建环境时，先在服务器上补齐系统依赖，再重跑测试。

## 5. 远端接口联调建议

所有烟测默认使用：

- `agent_profile=customer_support`
- `source_channel=websdk` 或 `wechat_kf`，仅用于观测和后台筛选
- 独立的 `session_id`，不要复用真实用户会话

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

预期：

- 得到 HiFleet 相关结论，必要时附官方链接。
- 返回文本客服化，不出现内部路径、工具名、命令或 JSON。

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
- 最终回复不出现 `综合摘要`、`查询1`、`HTMLLINK`、`agent_browser`、下载广告等内部或广告化文本。

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
- 不应调用随机船舶查询，也不应返回无关船舶的 MMSI、坐标和航速。

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

- 先确认是否发生在 HiFleet 页面，以及具体页面/操作。
- 不直接输出长篇平台排障模板。

### 5.5 测多轮上下文

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

- 第二轮不应被第一轮“上传航线失败”误导。
- 不应继续沿着平台排障回答。

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

- 第二轮可以复用上一轮船舶上下文。
- 不需要重复提供 MMSI。

### 5.7 测截图/语音/视频输入

通过调用方真实附件字段发送 HiFleet 海图或页面截图，并配文：

```text
这个圆圈是什么
```

预期：

- `route_trace.reasoning_trace.perception_summary` 能摘要截图内容、可见文字、疑似符号或页面问题。
- `route_trace.reasoning_trace.pipeline` 包含 `multimodal_input_parse`。
- 如果截图像 HiFleet 海图，应结合识别摘要回答或追问，不应当作普通闲聊。
- 如果截图不清晰，回复只追问一个关键补充，例如“请补一张更清晰的截图，最好圈出要确认的位置。”

### 5.8 测微信客服旧 `/run` 格式

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "content": {
      "query": {
        "prompt": [
          {"type": "voice", "content": {"url": "https://example.com/a.amr", "format": "amr"}},
          {"type": "text", "content": {"text": "帮我看一下这段语音里要查什么"}}
        ]
      }
    },
    "session_id": "wechat_kf:hifleet:openid_test:c_default",
    "user_id": "openid_test",
    "source_channel": "wechat_kf",
    "agent_profile": "customer_support"
  }'
```

预期：

- 服务端兼容 `content.query.prompt`，并归一化为当前轮 `messages`。
- `voice` / `image` / `video` 能进入多模态预处理。
- 回复仍是可直接发给微信用户的文本，不展示内部结构。

### 5.9 测船舶写操作

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "请更新船位 MMSI 414726000，经度 121.4737，纬度 31.2304，更新时间 2026-06-15 10:20:30"}],
    "session_id": "remote-ship-write-001",
    "user_id": "remote-dev",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

预期：

- 用户明确要求更新时才调用 `upload_ship_position` 或相应写工具。
- 缺字段时只追问一个最关键字段。
- 工具未返回成功时，不得宣称已更新成功。
- 成功时回复可说明“已按接口返回结果处理”，并保留必要风险提示。

## 6. 远端重点观察字段

如果通过后台或日志查看运行态，优先看：

- `llm_route`
- `phase_history`
- `route_trace.route`
- `route_trace.task_type`
- `route_trace.reasoning_trace.intent_agent_result`
- `route_trace.reasoning_trace.understanding_summary`
- `route_trace.reasoning_trace.update_params`
- `route_trace.reasoning_trace.perception_summary`
- `generated_tool_calls`
- `response_modalities`
- `output_assets`
- `check_result`

### 6.1 你想看到什么

- `route_trace.route` 为需求理解后的业务 route，例如 `knowledge`、`ship_update`、`chart_symbol`。
- `phase_history` 至少包含 `route`、`execute`、`finalize`。
- 知识弱命中时，`generated_tool_calls` 里可看到 browser 或 knowledge 工具调用。
- 多模态输入时，`perception_summary` 能说明音频转写、截图文字、视频摘要或附件识别结果。
- 写操作时，`update_params` 能看到来自当前输入/附件的规范化参数组。
- 链接型图文输出进入 `output_assets`，`response_modalities` 包含 `text` 和可能的 `link`。

### 6.2 你不想看到什么

- 最终回复里出现 `smart_search`、`agent-browser`、`reasoning_trace` 或原始 JSON。
- 最终回复里出现内部路径、命令、`.env`、`token`、`key`。
- 未明确写操作时调用船舶写工具。
- 工具失败时回复“已更新成功”。

## 7. 常见远端问题

### 7.1 本地通过，远端不触发 agent-browser

优先排查：

1. `agent-browser` 是否安装。
2. 服务器是否能访问 Bing 或目标公开网页。
3. 服务器是否能访问 `hifleet.com`。
4. 远端代码是否还是旧版。

### 7.2 多轮上下文表现和本地不一致

优先排查：

1. 远端是否启用了不同的 checkpointer。
2. 多 worker 下是否复用了同一 `session_id`。
3. 是否还有旧服务进程未重启。

### 7.3 日志里能看到工具调用，但客户回复不对

优先排查：

1. `sanitize_customer_output(...)` 是否触发了兜底。
2. `check_result.links_ok` 是否失败。
3. `config/profiles/customer_support.md` 是否为最新客服业务规则。
4. `config/agent_profiles.json` 是否注册了正确 skills 和工具权限。
5. 模型是否返回了可被最终收口层清洗的客户可见文本。

### 7.4 截图/语音/视频问题没有纳入当前轮

优先排查：

1. 请求体是否真的把 `image_url`、`input_audio`、`video_url` 传到 Agent。
2. 微信旧格式是否使用 `content.query.prompt[].type=image|voice|video`。
3. 远端 `multimodal_model` 是否配置为可用的 Seed Lite 模型。
4. 附件 URL 是否可被模型服务访问。
5. 当前服务是否仍在运行旧 graph。

### 7.5 微信客服没有进入 customer_support

优先排查：

1. 请求体 `agent_profile` 或请求头 `x-agent-profile` 是否为空或被网关改写；`employee_assistant` 现在会被兼容解析为 `customer_support`。
2. 如果没有传 Profile，服务端是否仍按默认值回退到 `customer_support`。
3. 旧 `content.query.prompt` 是否按数组传入。
4. `session_id` 是否稳定，是否被网关改写。
5. `source_channel` 是否正确写入日志；它不参与 Profile 判断。

## 8. 开发同学建议阅读顺序

1. [docs/AGENT_TECHNICAL_DOCUMENTATION.md](AGENT_TECHNICAL_DOCUMENTATION.md)
2. [docs/CUSTOMER_SUPPORT_KB_OPERATIONS.md](CUSTOMER_SUPPORT_KB_OPERATIONS.md)
3. [docs/API_MULTI_USER_INTEGRATION.md](API_MULTI_USER_INTEGRATION.md)
4. [docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md](CUSTOMER_SUPPORT_AGENT_REGRESSION.md)
5. [docs/agent_browser_fallback_integration.md](agent_browser_fallback_integration.md)
6. [docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md](CUSTOMER_SUPPORT_AGENT_REGRESSION.md)
7. [config/profiles/customer_support.md](../config/profiles/customer_support.md)
8. [src/agents/agent.py](../src/agents/agent.py)
9. [src/agents/customer_support_guard.py](../src/agents/customer_support_guard.py)
10. [src/skills/browser_verify/tools.py](../src/skills/browser_verify/tools.py)
