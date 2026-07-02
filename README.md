# coze_ai / HiFleet Agent 服务

本项目是部署在 HiFleet 服务器上的企业 Agent 服务，基于 `FastAPI + LangGraph + LangChain`，通过固定端口 `10123` 对外提供 `/run`、`/stream_run` 等 API，并内置 React 后台管理系统 `/admin-ui`。

当前不是单一客服 Bot，而是 **一个主 Agent + 多 Agent Profile** 的架构：

- `customer_support`：正式客服 Agent，面向客户、微信客服、WebSDK、CRM，采用轻量全模态 skills agent，支持知识检索、公开网页核验和船舶数据读写，禁用沙盒/Python/employee workspace。
- `employee_assistant`：`customer_support` 的兼容别名，继续可被旧调用方使用。
- `customer_ceshi`：测试/内部 Agent，保留知识问答、业务工具、文件检查和受控 Python 分析能力。

详细架构请优先阅读：`docs/AGENT_TECHNICAL_DOCUMENTATION.md`。
客服知识检索、平台操作类收口和授权写库请看：`docs/CUSTOMER_SUPPORT_KB_OPERATIONS.md`。
如果是在其他服务器上部署联调，先看：`docs/CUSTOMER_SUPPORT_REMOTE_DEPLOYMENT_RUNBOOK.md`。
如果需要让远端代码 Agent 快速接手检查和烟测，可直接使用：`docs/CUSTOMER_SUPPORT_REMOTE_AGENT_PROMPT.md`。

## 1. 核心目录

```text
coze_ai/
├── config/
│   ├── agent_llm_config.json       # 模型和工具配置
│   ├── agent_profiles.json         # customer_support / customer_ceshi 权限边界与别名
│   ├── profiles/                   # 各 Profile 的系统提示词
│   └── system_prompt_base.md       # 通用基础提示词
├── src/
│   ├── main.py                     # FastAPI 入口，/run、/stream_run、/admin-ui
│   ├── agents/                     # Agent 构建和 Profile 解析
│   ├── skills/                     # knowledge_qa / knowledge_admin / hifleet_ship_service / employee_workspace
│   ├── admin_api/                  # 后台管理 API
│   └── observability/              # 日志、工具调用、会话观测
├── frontend/                       # React 后台管理台
├── scripts/                        # 启动、测试、Profile smoke test
└── docs/                           # 架构、接口、后台、知识库文档
```

## 2. Agent Profile

Profile 解析优先级：请求体 `agent_profile` -> 请求头 `x-agent-profile` -> 默认 `customer_support`。`source_channel` 只用于日志、观测和业务来源记录，不参与 Profile 选择。

| Profile | 典型 source_channel | 能力 |
| --- | --- | --- |
| `customer_support` | `websdk`, `wechat_mp`, `wechat_kf`, `customer_api`, `crm` | 正式客服 profile；`employee_assistant` 作为兼容别名也会落到这里 |
| `customer_ceshi` | `admin_panel`, `internal_web`, `employee_api` | 测试/内部 profile；包含 employee workspace、表格检查和受控 Python 沙盒 |

## 3. 快速启动

```bash
cd /home/ecs-user/coze_ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

填写 `.env` 后启动：

```bash
bash scripts/start_unified_stack.sh
```

或安装 systemd：

```bash
bash scripts/install_systemd_service.sh
sudo systemctl restart hifleet-agent.service
sudo journalctl -u hifleet-agent.service -f
```

健康检查：

```bash
curl http://127.0.0.1:10123/health
```

后台入口：

```text
http://127.0.0.1:10123/admin-ui
```

## 4. 必需环境变量

```bash
COZE_WORKLOAD_IDENTITY_API_KEY=...
COZE_INTEGRATION_MODEL_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
COZE_INTEGRATION_BASE_URL=https://api.coze.cn
PGDATABASE_URL=postgresql://user:password@127.0.0.1:5432/postgres
COZE_CHECKPOINTER_MODE=postgres
ADMIN_API_KEY=your_admin_secret
SHIP_SERVICE_API_URL=...
SHIP_SERVICE_API_TOKEN=...
ark_websearch_api_key=...
```

授权写入本地知识库时需要额外配置：

```bash
HIFLEET_KB_UPDATE_KEY=<HIFLEET_KB_UPDATE_KEY>
```

数字员工 Python/文件能力可选变量：

```bash
HIFLEET_AGENT_ARTIFACT_DIR=/tmp/hifleet_agent_artifacts
HIFLEET_PY_SANDBOX_TIMEOUT_SEC=20
HIFLEET_PY_SANDBOX_MAX_CODE_CHARS=12000
```

## 5. 外部调用

同步接口：

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "船队轨迹为什么看不到？"}],
    "session_id": "websdk:tenant_a:user_100:c_001",
    "user_id": "user_100",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

微信客服现有旧 `/run` 调用可继续使用 `content.query.prompt`，服务端会归一化为 `messages`；新接入建议直接使用上面的 OpenAI 风格 `messages`。

流式接口：

```bash
curl -N -X POST http://127.0.0.1:10123/stream_run \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"请流式介绍HiFleet轨迹功能"}],"session_id":"websdk:u1:c1","user_id":"u1","source_channel":"websdk","agent_profile":"customer_support"}'
```

更多接入规范：`docs/API_MULTI_USER_INTEGRATION.md`。

## 6. 后台管理

后台管理系统包含：

- Dashboard：KPI、趋势、渠道/路由/Profile 分布、高风险会话。
- Sessions：会话列表、消息回放、Profile 筛选、日志联动。
- Logs：请求、响应、工具链、错误、Trace。
- Chat Debug：多会话调试、SSE、附件上传、显式 Profile 测试。
- API Playground：构造 `/run` 和 `/stream_run` 请求。

使用手册：`docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`。

异地部署联调：`docs/CUSTOMER_SUPPORT_REMOTE_DEPLOYMENT_RUNBOOK.md`

远端 Agent 检查提示词：`docs/CUSTOMER_SUPPORT_REMOTE_AGENT_PROMPT.md`

`agent-browser` 受控兜底：`docs/agent_browser_fallback_integration.md`

## 7. 测试

```bash
.venv/bin/python -m py_compile \
  src/main.py src/agents/agent.py src/agents/profiles.py \
  src/skills/skill_loader.py src/skills/employee_workspace/tools.py

PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py

cd frontend
npm run build
```

## 8. 文档入口

- 主架构：`docs/AGENT_TECHNICAL_DOCUMENTATION.md`
- 客服知识检索与授权写库：`docs/CUSTOMER_SUPPORT_KB_OPERATIONS.md`
- 异地部署联调：`docs/CUSTOMER_SUPPORT_REMOTE_DEPLOYMENT_RUNBOOK.md`
- 远端 Agent 检查提示词：`docs/CUSTOMER_SUPPORT_REMOTE_AGENT_PROMPT.md`
- `agent-browser` 兜底链：`docs/agent_browser_fallback_integration.md`
- 外部 API 接入：`docs/API_MULTI_USER_INTEGRATION.md`
- 后台使用：`docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`
- 知识库维护：`docs/KNOWLEDGE_BASE_GUIDE.md`
