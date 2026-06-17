# Customer Support 异地服务器部署联调手册

本文面向把当前项目部署到其他 Linux 服务器上测试的开发人员，目标是帮助大家快速确认：

- 服务是否按当前代码真实启动
- `customer_support` 主链是否走到了最新实现
- `agent-browser`、上下文压缩、knowledge/harness 是否在远端生效

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
- 上下文压缩是否生效
- intent/planner 是否使用压缩后的相关历史

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

### 5.2 测多轮上下文压缩

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

### 5.3 测上下文追问仍可用

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

## 6. 远端重点观察字段

如果你通过后台或日志查看运行态，优先看：

- `phase_history`
- `route_trace.route`
- `route_trace.task_type`
- `route_trace.fallback_reason`
- `generated_tool_calls`
- `check_result`

### 6.1 你想看到什么

- `phase_history` 包含 `executed` 或 `delegated`
- 知识链弱命中时，`generated_tool_calls` 里可看到 `agent_browser_deep_search`
- `fallback_reason` 可能出现 `smart_search_empty_agent_browser_fallback`

### 6.2 你不想看到什么

- 最终回复里出现 `smart_search`
- 最终回复里出现 `agent-browser`
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

## 8. 开发同学建议阅读顺序

1. [docs/AGENT_TECHNICAL_DOCUMENTATION.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/AGENT_TECHNICAL_DOCUMENTATION.md)
2. [docs/agent_browser_fallback_integration.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/agent_browser_fallback_integration.md)
3. [docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/docs/CUSTOMER_SUPPORT_AGENT_REGRESSION.md)
4. [src/agents/agent.py](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/src/agents/agent.py)
5. [src/agents/customer_support_router.py](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/src/agents/customer_support_router.py)
6. [src/skills/browser_verify/tools.py](/Users/raymondlu/LocalProject/AIPM/智能客服/客服开发/本地agent/hifleet-agent/src/skills/browser_verify/tools.py)
