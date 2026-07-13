# 管理台与观测指南

本文合并原“后台使用指南”和“管理平台开发者总览”，覆盖管理台入口、核心页面、后端代码地图和排障方法。

## 1. 入口

```text
http://127.0.0.1:10123/admin-ui
```

如果配置了 `ADMIN_API_KEY`，所有 `/admin/*` 请求需要：

```bash
-H "x-admin-api-key: ${ADMIN_API_KEY}"
```

## 2. 启动、停止与状态检查

### 2.1 启动

推荐使用统一启动脚本：

```bash
cd /home/ecs-user/coze_ai
nohup scripts/start_system_service.sh > /tmp/coze_ai_10123.log 2>&1 &
```

脚本会：

- 设置 `COZE_WORKSPACE_PATH`
- 优先使用 `.venv`
- 由 `src/main.py` 加载 `.env`
- 固定监听 `10123`
- 在需要时构建后台管理前端

### 2.2 停止

```bash
pgrep -af 'src/main.py -m http -p 10123'
kill <pid>
```

如果存在父子多进程，先杀父进程；必要时再检查端口是否释放。

### 2.3 重启

```bash
cd /home/ecs-user/coze_ai
pkill -f 'src/main.py -m http -p 10123' || true
sleep 3
nohup scripts/start_system_service.sh > /tmp/coze_ai_10123.log 2>&1 &
```

注意：不要同时启动多个 10123 实例，否则 worker 会报 `Address already in use`。

### 2.4 健康检查

本机环境可能配置 HTTP 代理，检查本地服务时建议绕过代理：

```bash
curl --noproxy '*' -i http://127.0.0.1:10123/health
```

期望：

```json
{"status":"ok","message":"Service is running"}
```

### 2.5 端口占用检查

```bash
ss -ltnp | grep ':10123'
pgrep -af 'src/main.py -m http -p 10123'
```

### 2.6 日志

启动日志：

```bash
tail -f /tmp/coze_ai_10123.log
```

应用日志默认位置：

```text
/tmp/app/work/logs/bypass/app.log
```

## 3. 管理台架构

```mermaid
flowchart LR
    UI[React /admin-ui] --> Client[frontend/src/api/client.ts]
    Client --> Router[src/admin_api/router.py]
    Router --> Service[src/admin_api/service.py]
    Service --> Repo[src/observability/repository.py]
    Repo --> DB[(Postgres observability schema)]
    Router --> Test[/admin/test/run]
    Test --> Agent[/run or /stream_run]
```

管理台不单独部署服务，静态资源由 `src/main.py` 挂载在 `/admin-ui`，API 挂载在 `/admin/*`。

## 4. 页面能力

| 页面 | 用途 |
| --- | --- |
| Dashboard | 调用量、成功率、延迟、工具成功率、渠道/Profile 分布、高风险会话 |
| Sessions | 按用户、会话、Profile 回放消息 |
| Chat Debug | 多会话调试，支持 `agent_profile`、附件、SSE、案例保存 |
| Logs | 按 run_id/session_id/user_id/source_channel/profile/route/status 检索 |
| API Playground | 构造 `/run`、`/stream_run` 请求 |
| Config | 配置中心骨架页 |

## 5. 关键接口

| 接口 | 说明 |
| --- | --- |
| `GET /admin/dashboard/summary` | Dashboard 聚合 |
| `GET /admin/logs` | 调用日志列表 |
| `GET /admin/logs/{run_id}` | 单次调用详情 |
| `GET /admin/sessions` | 会话列表 |
| `GET /admin/sessions/{session_id}` | 会话详情 |
| `POST /admin/test/run` | 代理调用 Agent |
| `GET /admin/chat-debug/sessions` | Chat Debug 案例列表 |
| `POST /admin/chat-debug/sessions` | 保存调试案例 |

常用查询：

```bash
curl "http://127.0.0.1:10123/admin/logs?page=1&page_size=20&agent_profile=customer_support&status=error" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

## 6. 代码地图

后端：

| 文件 | 说明 |
| --- | --- |
| `src/admin_api/router.py` | `/admin/*` HTTP 路由 |
| `src/admin_api/schemas.py` | 请求/响应 schema |
| `src/admin_api/service.py` | Dashboard、Logs、Sessions、测试代理聚合 |
| `src/observability/repository.py` | 观测 SQL 查询 |
| `src/observability/writer.py` | 异步写入 API 调用、工具调用、错误 |
| `src/observability/sql/` | 数据库 schema 初始化 |

前端：

| 文件 | 说明 |
| --- | --- |
| `frontend/src/App.tsx` | 页面路由 |
| `frontend/src/layouts/AdminShell.tsx` | 管理台布局 |
| `frontend/src/api/client.ts` | API client |
| `frontend/src/pages/*` | Dashboard、Logs、Sessions、Chat Debug、Playground |

## 7. 观测数据模型

主要表：

- `observability.api_calls`：请求主记录。
- `observability.tool_invocations`：工具调用明细。
- `observability.agent_errors`：运行错误。
- `observability.chat_debug_sessions`：Chat Debug 案例。

重要字段：

- `run_id`：单次调用主键。
- `session_id`：多轮会话主键。
- `user_id`：用户定位。
- `source_channel`：渠道。
- `agent_profile`：从 request JSON 派生。
- `route` / `task_type`：客服 phase graph 在 `plan` 阶段的意图结果。
- `status=degraded_success`：外部客服递归超限时的受控业务兜底；HTTP 为 200，但同一 `run_id` 仍应在 `agent_errors` 中看到原始异常。

## 8. 排障流程

```mermaid
flowchart TD
    Symptom[用户反馈/接口错误] --> Logs[Logs 按 session_id/run_id 查询]
    Logs --> Detail[打开日志详情]
    Detail --> Route[检查 profile/phase/route/task_type/tool_bundle]
    Route --> Tools[检查 tool_call_sequence 与 tool_invocations]
    Tools --> Latency[定位 latency_hotspot]
    Tools --> Error[检查 agent_errors]
    Latency --> Fix[修复路由/工具/API/权限]
    Error --> Fix
```

客服 Agent 重点检查：

- `phase_history` 是否按预期经过 `preprocess -> knowledge|delegate|ship_update -> finalize`。
- `route` 是否符合问题类型；`understanding_to_knowledge_chain` 表示需求理解直接触发知识检索。
- `tool_bundle` 是否收缩正确。
- `entity_resolution` 是否抽到了 MMSI/IMO/船名/区域/日期。
- `tool_call_sequence` 是否有不必要调用。
- `fallback_reason` 是否暴露授权、无数据或校验失败。
- `reasoning_trace.understanding_result.evidence_required` 是否要求三层证据核验；此类请求不应因单层 `can_answer=true` 提前结束。
- 出现 `degraded_success` 时，同时检查 `agent_errors.stack_trace`、`fallback_reason=graph_recursion_limit` 与 delegate 的 `recursion_limit`，不要因为 API 状态为 200 而忽略异常。

## 9. 新增 Profile 或工具后的检查

1. 更新 `config/agent_profiles.json`。
2. 更新 `config/agent_llm_config.json` 工具列表。
3. 跑：

```bash
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, 'src')
from skills import SkillLoader
print(SkillLoader.validate_registry_consistency())
PY
```

4. 在 Chat Debug 中分别测试 `customer_support` 和 `customer_ceshi`；旧 `employee_assistant` 只用于验证是否兼容落到 `customer_support`。
5. 到 Logs 检查 profile、route、tool 调用是否正确落库。
