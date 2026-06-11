# HiFleet Agent 服务技术文档

本文是当前系统的主架构文档，面向接手开发、部署、排障和二次扩展的工程人员。当前系统定位为 **HiFleet 企业内部 Agent 服务**：同一套 FastAPI 服务对外提供客服能力，对内提供数字员工能力，并内置后台管理系统用于观测、调试和测试。

## 1. 当前定位

系统运行在本地服务器，通过 `0.0.0.0:10123` 对外暴露服务，核心能力分为两类：

- `customer_support`：对外客服 Agent，面向客户、渠道服务、CRM 等外部接入方。
- `employee_assistant`：内部数字员工 Agent，面向内部员工和管理后台，支持知识问答、业务工具、文件处理和受控 Python 分析。

两个 Agent Profile 共用同一个主 Agent 构建链路，但拥有不同的 prompt、skill、工具权限和观测维度。这样避免重复维护两套 Agent，同时保证外部客服和内部员工的权限边界清晰。

## 2. 总体架构

```mermaid
flowchart LR
    External[外部客户/渠道系统] -->|POST /run or /stream_run| API[FastAPI: src/main.py]
    Employee[内部员工/后台管理台] -->|/admin-ui or /admin/test/run| API
    API --> Profile[Profile Resolver\nagent_profile + source_channel]
    Profile --> Agent[LangGraph Agent\nsrc/agents/agent.py]
    Agent --> Prompt[基础 Prompt + Profile Prompt]
    Agent --> Skills[Skill Loader\nsrc/skills/*]
    Skills --> KQ[knowledge_qa\n知识库/联网搜索]
    Skills --> Ship[hifleet_ship_service\n船舶业务工具]
    Skills --> Workspace[employee_workspace\n文件/Python沙盒]
    Agent --> Memory[Checkpointer\nPostgres or Memory]
    API --> Obs[Observability Writer]
    Skills --> Obs
    Obs --> PG[(Postgres\nobservability schema)]
    Admin[Admin API /admin/*] --> PG
    UI[React Admin UI\n/admin-ui] --> Admin
```

### 2.1 请求执行链路

```mermaid
sequenceDiagram
    participant C as Caller
    participant M as src/main.py
    participant P as profiles.py
    participant A as agents/agent.py
    participant S as Skill Tools
    participant O as Observability
    C->>M: POST /run 或 /stream_run
    M->>M: 解析 messages/session_id/user_id/source_channel
    M->>P: resolve_profile_id(agent_profile, source_channel, header)
    P-->>M: customer_support 或 employee_assistant
    M->>A: 构建/获取 Agent 实例
    A->>A: 合成 base prompt + profile prompt + skill docs
    A->>S: 按 profile 加载允许工具
    S-->>A: 工具结果
    A-->>M: 同步结果或 SSE chunk
    M->>O: 写入 api_calls / tool_invocations / agent_errors
    M-->>C: JSON 或 text/event-stream
```

### 2.2 后台管理链路

```mermaid
flowchart TD
    Browser[浏览器 /admin-ui] --> React[frontend/src/pages]
    React --> Client[frontend/src/api/client.ts]
    Client --> AdminAPI[/admin/*]
    AdminAPI --> Service[src/admin_api/service.py]
    Service --> Repo[src/observability/repository.py]
    Repo --> DB[(observability.*)]
    AdminAPI --> AgentProxy[/admin/test/run]
    AgentProxy --> RunAPI[/run 或 /stream_run]
```

后台管理系统不单独部署服务，构建后的前端静态文件由主 FastAPI 服务挂载在 `/admin-ui`，后台 API 挂载在 `/admin/*`。

### 2.3 模型路由层

当前主链路已引入统一的模型路由层：

- 共享配置文件：`config/agent_llm_config.json`
- 共享配置模块：`src/llm_config.py`
- 运行态路由状态：`src/utils/llm_route_state.py`

默认规则：

- 纯文本请求：默认使用 `doubao-seed-2-0-pro-260215`
- 图片/音频/视频等多模态请求：默认使用 `doubao-seed-2-0-lite-260428`
- 深度思考：默认 `disabled`，可在后台配置页切换为 `enabled` 或后续扩展 `auto`

路由时机：

1. `src/main.py` 在 `/run`、`/stream_run` 入口将请求统一归一化为 `messages`。
2. 根据消息内容是否包含 `image_url`、`input_audio`、`video_url` 等片段判断 `text/multimodal`。
3. 将解析出的 `model/modality/thinking_type` 写入 `payload.llm_route`，并同步写入 `ContextVar`。
4. `src/agents/agent.py` 构建 `ChatOpenAI` 时不再只读静态配置，而是优先读取当前请求的 `llm_route`。

这样可以保证同一套 Agent 拓扑在不同输入模态下自动切换模型，而不需要调用方自己传模型名。

## 3. Profile 设计

### 3.1 为什么选择“一个主 Agent + 多 Profile”

当前更适合采用“一个主 Agent，多 Profile 权限边界”的架构，而不是完全独立的多个 Agent 服务。原因：

- 客服和数字员工共享大量基础能力：HiFleet 知识库、业务工具、会话记忆、观测链路、后台调试。
- 真正需要隔离的是工具权限、提示词行为、搜索策略、沙盒能力和调用来源。
- 用 Profile 可以保持接口稳定，外部调用方仍只需要调用 `/run` 或 `/stream_run`。
- 后续新增角色时只需加配置和 prompt，不必复制主执行链路。

需要升级为独立 Agent 服务的条件：模型完全不同、数据隔离要求强、部署/计费/审计完全独立、工具集合几乎无重叠。当前项目尚未达到这个复杂度。

### 3.2 Profile 解析优先级

代码入口：`src/agents/profiles.py`

解析顺序：

1. 请求体中的 `agent_profile`。
2. 请求头 `x-agent-profile`。
3. 根据 `source_channel` 映射。
4. 回退到默认 `customer_support`。

配置文件：`config/agent_profiles.json`

### 3.3 当前 Profile 定义

| Profile | 面向对象 | 默认渠道 | Skills | 禁用工具 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `customer_support` | 外部客户、客服渠道、CRM | `websdk`, `wechat_mp`, `wechat_kf`, `customer_api`, `crm` | `knowledge_qa`, `hifleet_ship_service` | `upload_ship_position`, `update_ship_static_info` | 强调需求理解、公开信息检索、客户友好表达，不允许文件/Python/写操作 |
| `employee_assistant` | 内部员工、后台调试、内部系统 | `admin_panel`, `internal_web`, `employee_api`, `employee_assistant` | `knowledge_qa`, `hifleet_ship_service`, `employee_workspace` | 无 | 支持内部知识问答、表格检查、受控 Python 分析和 artifact 生成 |

### 3.4 客服 Agent 能力边界

客服 Agent 的目标是提升用户体验和回复准确性：

- 优先理解用户真实需求，不直接输出“无法回答”。
- 对知识库缺失但公开网页可检索的问题，优先调用 `smart_search` 检索公开信息。
- 对平台使用、业务问题、船舶基础查询，结合知识库和业务工具回答。
- 回复客户时避免暴露内部配置、工具名、异常堆栈、数据库字段。
- 不执行文件处理、Python 代码、内部写操作和高风险业务更新。

维护入口：

- Prompt：`config/profiles/customer_support.md`
- Skills：`config/agent_profiles.json` 中 `customer_support.skills`
- 工具禁用：`config/agent_profiles.json` 中 `customer_support.disabled_tools`

### 3.5 数字员工 Agent 能力边界

数字员工 Agent 面向内部场景：

- 继承客服知识问答与 HiFleet 业务工具能力。
- 支持公开 CSV/XLS/XLSX 文件链接预下载：`download_public_file_to_artifact`。
- 支持 CSV/XLS/XLSX 文件检查：`inspect_tabular_file`。
- 支持短脚本 Python 分析：`run_sandboxed_python`。
- 生成报价表、统计结果、临时 artifact 时写入 `HIFLEET_AGENT_ARTIFACT_DIR`。
- 需要通过内部渠道或显式 `agent_profile=employee_assistant` 调用。

维护入口：

- Prompt：`config/profiles/employee_assistant.md`
- 工具：`src/skills/employee_workspace/tools.py`
- Skill 文档：`src/skills/employee_workspace/SKILL.md`

## 4. Skills 与工具

```mermaid
flowchart LR
    Profile[AgentProfile] --> Loader[SkillLoader]
    Loader --> QA[knowledge_qa]
    Loader --> Ship[hifleet_ship_service]
    Loader --> Emp[employee_workspace]
    QA --> smart_search
    Ship --> ship_tools[船舶查询/轨迹/PSC/更新类工具]
    Emp --> inspect_tabular_file
    Emp --> run_sandboxed_python
```

当前 Skill：

- `knowledge_qa`：知识库、站内资料、联网搜索能力，核心工具为 `smart_search`。
- `hifleet_ship_service`：HiFleet 船舶业务接口工具。
- `employee_workspace`：内部文件处理和 Python 沙盒工具。

工具加载策略：

1. Agent 根据当前 Profile 读取允许的 skills。
2. `SkillLoader.get_tools_by_skill_names()` 加载对应工具。
3. 再应用 `disabled_tools` 做最终过滤。
4. 工具结果通过 `ToolResult` 统一写入 observability。

## 5. 数字员工执行闭环与 Docker 沙盒

当前 `employee_assistant` 已从“工具增强问答 Agent”升级为显式的执行闭环，核心实现位于 `src/agents/agent.py` 和 `src/skills/employee_workspace/tools.py`。

### 5.1 LangGraph 闭环

```mermaid
flowchart TD
    Route[route] -->|表格分析/产物任务| Plan[plan]
    Route -->|普通内部问答| Delegate[delegate]
    Plan --> Act[act]
    Act --> Check[check]
    Check -->|exit_code=0 且产物校验通过| Finalize[finalize]
    Check -->|失败且 loop_count < max| Loop[loop]
    Loop --> Act
    Check -->|失败且达到上限| Fail[fail]
```

执行规则：

- `plan`：如果输入是公开 URL，先调用 `download_public_file_to_artifact`；随后强制调用 `inspect_tabular_file`，把 `columns/dtypes/missing_values/preview/schema` 写入 Agent State。
- `act`：LLM 只允许基于 `plan` 返回的真实 Schema 生成 Python 代码。
- `check`：调用 `run_sandboxed_python`，检查 `exit_code`，并在给定 `expected_artifact` 时额外校验文件存在且大小大于 0。
- `loop`：把上一轮 `stderr`、`artifact_check`、旧代码和 `loop_count` 回灌给模型继续修复。
- 熔断：`HIFLEET_EMPLOYEE_MAX_LOOPS`，当前默认 `4`。

### 5.2 Docker sibling container 沙盒

当前 Python 执行不再落到宿主机 `subprocess.run`，而是通过 Docker SDK 动态拉起兄弟容器：

- `network_mode="none"`：完全断网。
- `read_only=True`：根文件系统只读。
- `mem_limit=512m`、`cpu_quota=50000`：限制资源消耗。
- `user="1000:1000"`：非 root 身份运行。
- `tmpfs=/tmp`：容器内临时目录隔离。
- 共享卷：通过 `HIFLEET_PY_SANDBOX_VOLUME` 挂载 artifacts 目录。
- 输入文件：调用前复制到 job 目录，并通过 `INPUT_FILE` 传入容器，避免脚本直接访问宿主机路径。

### 5.3 AST 前置守门

在正式起容器前，`run_sandboxed_python` 会先执行 AST 静态扫描：

- 允许 import：`pandas`、`numpy`、`openpyxl`、`json`、`datetime`、`math` 等白名单。
- 禁止高危内置：`eval`、`exec`、`compile`、`getattr`、`setattr`、`__import__`。
- 禁止任何双下划线属性与变量访问。
- 命中后直接返回 `ERR_SANDBOX_SECURITY`，并写入 `observability.agent_errors`。

## 6. Harness 与 Loop 工程策略

当前系统已接入 `cozeloop.flush()`、结构化日志和工具观测。复杂任务建议按以下方式工程化：

```mermaid
flowchart TD
    Task[复杂任务] --> Plan[计划/约束生成]
    Plan --> Act[执行工具或代码]
    Act --> Check[结果校验]
    Check -->|失败可重试| Act
    Check -->|通过| Answer[面向用户总结]
    Act --> Trace[写入工具日志/Trace]
    Check --> Trace
```

落地原则：

- 客服问题：采用“理解需求 -> 检索 -> 交叉验证 -> 客户化回复”的轻量 loop。
- 数据分析：采用“确认文件 -> 检查字段 -> 运行 Python -> 校验产物 -> 汇总结论”的受控 loop。
- 高风险动作：必须有权限边界、参数确认、可观测记录；外部客服 profile 默认禁用写操作。
- 评测 harness：建议沉淀 `scripts/test_agent_profiles.py`、典型客服问题集、典型表格处理任务集，作为发布前 smoke/regression。

## 7. 后台管理系统

后台页面：

- Dashboard：KPI、趋势、渠道/路由/Profile 分布、高风险会话。
- Sessions：会话列表、消息回放、Profile 筛选、日志联动。
- Logs：按时间、状态、渠道、Profile、关键词检索请求，查看 request/response/tools/errors/trace。
- Chat Debug：多会话调试、附件上传、显式 `agent_profile`、SSE 事件查看、案例保存。默认提供 `自动路由（按配置页）` 选项，也允许临时手动覆盖模型。
- API Playground：同步/流式接口测试，支持覆盖 `source_channel` 和 `agent_profile`。
- Config：模型配置中心，支持设置默认文本模型、多模态模型、自定义模型 ID 和深度思考开关。

观测字段：

- `run_id`：单次调用唯一键。
- `session_id`：会话隔离和多轮记忆键。
- `user_id`：用户定位键。
- `source_channel`：来源渠道。
- `agent_profile`：由请求体中的 `agent_profile` 写入 `request_json`，后台查询时派生展示。

## 8. 后台模型配置接口

### 8.1 管理台配置接口

```bash
curl http://127.0.0.1:10123/admin/config/llm

curl -X PUT http://127.0.0.1:10123/admin/config/llm \
  -H 'Content-Type: application/json' \
  -d '{
    "text_model": "doubao-seed-2-0-pro-260215",
    "multimodal_model": "doubao-seed-2-0-lite-260428",
    "thinking_type": "disabled"
  }'
```

返回字段：

- `text_model`
- `multimodal_model`
- `thinking_type`
- `deep_thinking_enabled`
- `text_model_presets`
- `multimodal_model_presets`

### 8.2 真实联调结论

2026-06-11 已完成真实运行态联调，结论如下：

- `GET/PUT /admin/config/llm` 可正常读写并即时生效。
- 纯文本 `/run` 请求稳定命中 `doubao-seed-2-0-pro-260215`。
- 多模态 `/run` 请求稳定命中 `doubao-seed-2-0-lite-260428`。
- 早期出现过一次 `llm_route=model=lite` 但 `response_metadata.model_name=pro` 的异常；后续通过顺序回归、OpenAI SDK 直连、LangChain `ChatOpenAI` 直连、`bind_tools` 最小复现均未再次出现。当前判断为测试时并行更新配置和并发请求造成的运行态竞态，不是稳定代码缺陷。

## 9. 外部接口

### 8.1 健康检查

```bash
curl http://127.0.0.1:10123/health
```

### 8.2 同步调用 `/run`

接口兼容说明：当前服务会把 `messages` 作为主格式，同时兼容 `input`、`text`、`content.query.prompt`。旧调用方可以继续使用原格式完成迁移，但新接入应统一改为 `messages`，这样便于后续维护、调试和多模态扩展。

客服调用示例：

```json
{
  "messages": [{"role": "user", "content": "船队轨迹为什么看不到？"}],
  "session_id": "websdk:tenant_a:user_100:c_001",
  "user_id": "user_100",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

数字员工调用示例：

```json
{
  "messages": [{"role": "user", "content": "读取 /tmp/orders.xlsx，统计各客户报价金额。"}],
  "session_id": "employee:finance:u_1:c_001",
  "user_id": "employee_1",
  "source_channel": "employee_api",
  "agent_profile": "employee_assistant"
}
```

### 8.3 流式调用 `/stream_run`

响应为 SSE：`text/event-stream`。

```bash
curl -N -X POST http://127.0.0.1:10123/stream_run \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"请介绍HiFleet轨迹功能"}],"session_id":"websdk:u1:c1","user_id":"u1","source_channel":"websdk"}'
```

### 8.4 取消执行

```bash
curl -X POST http://127.0.0.1:10123/cancel/<run_id>
```

### 8.5 OpenAI 兼容接口

```text
POST /v1/chat/completions
```

用于兼容部分 Chat Completions 客户端。业务集成优先使用 `/run` 或 `/stream_run`，因为这两个接口完整保留 `session_id`、`source_channel`、`agent_profile` 等业务字段。

## 9. 部署

### 9.1 最小环境变量

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

数字员工文件/Python 可选变量：

```bash
HIFLEET_AGENT_ARTIFACT_DIR=/tmp/hifleet_agent_artifacts
HIFLEET_PY_SANDBOX_TIMEOUT_SEC=20
HIFLEET_PY_SANDBOX_MAX_CODE_CHARS=12000
HIFLEET_PY_SANDBOX_STDIO_CHARS=8000
HIFLEET_PY_SANDBOX_IMAGE=hifleet/python-sandbox:3.11
HIFLEET_PY_SANDBOX_IMAGE_CANDIDATES=hifleet/python-sandbox:3.11,swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim
HIFLEET_PY_SANDBOX_AUTO_PULL=1
HIFLEET_PY_SANDBOX_VOLUME=coze_ai_shared-artifacts
HIFLEET_PY_SANDBOX_VOLUME_MOUNT=/workspace/artifacts
HIFLEET_PY_SANDBOX_MEM_LIMIT=512m
HIFLEET_PY_SANDBOX_CPU_QUOTA=50000
HIFLEET_PY_SANDBOX_USER=1000:1000
HIFLEET_EMPLOYEE_MAX_LOOPS=4
HIFLEET_PUBLIC_FILE_TIMEOUT_SEC=30
HIFLEET_PUBLIC_FILE_MAX_MB=100
```

### 9.2 启动

```bash
cd /home/ecs-user/coze_ai
source .venv/bin/activate
bash scripts/start_unified_stack.sh
```

或安装 systemd：

```bash
bash scripts/install_systemd_service.sh
sudo systemctl restart hifleet-agent.service
sudo journalctl -u hifleet-agent.service -f
```

服务端口固定为 `10123`，对外访问前需要放通主机防火墙、云安全组和反向代理。

### 9.3 Docker Compose 开发编排

当前仓库已新增 `docker-compose.dev.yml`，用于本地或测试环境验证 employee sandbox：

```bash
docker compose -f docker-compose.dev.yml up --build
```

关键挂载：

- `/var/run/docker.sock:/var/run/docker.sock`
- `coze_ai_shared-artifacts:/workspace/artifacts`

如果 `run_sandboxed_python` 返回 `Permission denied` 或 `Error while fetching server API version`，优先检查主服务容器/当前用户是否有权访问 Docker socket。

## 10. 测试与发布前检查

后端编译：

```bash
.venv/bin/python -m py_compile \
  src/main.py \
  src/agents/agent.py \
  src/agents/profiles.py \
  src/skills/skill_loader.py \
  src/skills/employee_workspace/tools.py \
  src/observability/repository.py \
  src/admin_api/router.py \
  src/admin_api/schemas.py \
  src/admin_api/service.py
```

Profile smoke test：

```bash
PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py
```

Employee workspace 与闭环 smoke test：

```bash
PYTHONPATH=src .venv/bin/python scripts/test_employee_workspace.py
PYTHONPATH=src .venv/bin/python scripts/test_employee_agent_loop.py
bash scripts/prepare_employee_sandbox_image.sh
```

前端构建：

```bash
cd frontend
npm run build
```

接口 smoke：

```bash
curl http://127.0.0.1:10123/health
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"你好"}],"session_id":"smoke:s1","user_id":"smoke","source_channel":"websdk"}'
```

## 11. 维护手册

### 11.1 新增 Profile

1. 在 `config/agent_profiles.json` 增加 profile 配置。
2. 在 `config/profiles/` 增加 profile prompt。
3. 明确 `source_channels`、`skills`、`disabled_tools`、`requires_auth`、`sandbox_enabled`。
4. 更新 `scripts/test_agent_profiles.py` 增加权限边界测试。
5. 在后台 Logs/Dashboard 验证 profile 可观测。

### 11.2 新增 Skill

1. 新建 `src/skills/<skill_name>/SKILL.md` 和 `tools.py`。
2. 在 `src/skills/skill_loader.py` 注册工具。
3. 在 `config/agent_profiles.json` 将 skill 授权给对应 profile。
4. 工具返回统一使用 `ToolResult` 并写入 observability。
5. 为高风险工具设置 profile 限制和参数校验。

### 11.3 知识库维护

知识库和搜索维护参考 `docs/KNOWLEDGE_BASE_GUIDE.md`。客服场景要优先维护：平台使用说明、常见错误、业务术语、公开帮助页面、船舶查询边界。

### 11.4 清理策略

- 旧 `src/workflows` 代码已删除，生产入口不再保留 workflow 分支。
- `src/tools/smart_search_tool.py` 仅作为兼容 wrapper，实际实现位于 `src/skills/knowledge_qa/tools.py`。
- 旧节点级接口 `/node_run/{node_id}` 和 `/graph_parameter` 已从主服务移除。
- 新增文档优先更新本文，避免多份架构说明互相冲突。
