# Agent 管理平台开发者总览（归档）

> 归档说明：当前管理台使用与开发说明已合并到
> `docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`。本文保留为历史参考，不再作为主入口。

本文面向接手后台管理系统和观测链路的开发人员。主 Agent 架构见 `docs/AGENT_TECHNICAL_DOCUMENTATION.md`，后台使用手册见 `docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`。

## 1. 后台架构

```mermaid
flowchart LR
    UI[React /admin-ui] --> Client[frontend/src/api/client.ts]
    Client --> Router[src/admin_api/router.py]
    Router --> Service[src/admin_api/service.py]
    Service --> Repo[src/observability/repository.py]
    Repo --> DB[(Postgres observability schema)]
    Service --> Proxy[/admin/test/run]
    Proxy --> Agent[/run or /stream_run]
```

后台不单独部署，构建产物由 `src/main.py` 挂载在 `/admin-ui`，API 挂载在 `/admin/*`。

## 2. 关键代码地图

后端：

- `src/admin_api/router.py`：后台 HTTP 路由和 query 参数。
- `src/admin_api/schemas.py`：后台请求 schema。
- `src/admin_api/service.py`：后台业务聚合、预览字段、测试代理。
- `src/observability/repository.py`：日志列表、详情、会话、Dashboard 聚合 SQL。
- `src/observability/sql/`：observability schema 初始化 SQL。
- `src/observability/writer.py`：异步写入入口。

前端：

- `frontend/src/App.tsx`：页面路由。
- `frontend/src/layouts/AdminShell.tsx`：全局导航、时间范围、详情抽屉。
- `frontend/src/api/client.ts`：后台 API client。
- `frontend/src/types.ts`：Dashboard、Logs、Sessions 数据类型。
- `frontend/src/pages/DashboardPage.tsx`：总览。
- `frontend/src/pages/LogsPage.tsx`：日志检索。
- `frontend/src/pages/LogDetailPage.tsx`：日志详情。
- `frontend/src/pages/SessionPage.tsx`：会话中心。
- `frontend/src/pages/ChatDebugPage.tsx`：对话调试。
- `frontend/src/pages/TestPage.tsx`：API Playground。

## 3. 数据模型

主要表：

- `observability.api_calls`：请求主表。
- `observability.tool_invocations`：工具调用明细。
- `observability.agent_errors`：运行错误。
- `observability.chat_debug_sessions`：Chat Debug 案例持久化。

重要键：

- `run_id`：单次调用主键。
- `session_id`：多轮上下文和会话回放键。
- `user_id`：用户定位键。
- `source_channel`：渠道维度。
- `agent_profile`：从 `api_calls.request_json ->> 'agent_profile'` 派生，不需要新增数据库列。

## 4. Profile 可观测性

后台已支持：

- Logs 列表按 `agent_profile` 查询和展示。
- Logs 详情展示 Profile 和渠道。
- Sessions 列表按 `agent_profile` 查询和展示。
- Dashboard 展示 `distribution.by_profile`。
- Chat Debug 和 API Playground 可显式传入 `agent_profile`。

如果新增 Profile：

1. 更新 `config/agent_profiles.json` 和 `config/profiles/*.md`。
2. 确认 `/run` 写入的 request_json 包含 `agent_profile`。
3. 在 Dashboard/Logs/Sessions 通过新 Profile 筛选验证。
4. 扩展 `scripts/test_agent_profiles.py` 的权限边界断言。

## 5. 后台接口

| 接口 | 说明 |
| --- | --- |
| `GET /admin/dashboard/summary` | 总览 KPI、趋势、分布、高风险会话 |
| `GET /admin/logs` | 请求日志列表，支持 `agent_profile` |
| `GET /admin/logs/{run_id}` | 单次调用详情 |
| `GET /admin/sessions` | 会话列表，支持 `agent_profile` |
| `GET /admin/sessions/{session_id}` | 会话时间线 |
| `POST /admin/test/run` | 代理 `/run` 或 `/stream_run` |
| `GET /admin/chat-debug/sessions` | 调试会话列表 |
| `PUT /admin/chat-debug/sessions/{session_key}` | 保存调试案例 |
| `DELETE /admin/chat-debug/sessions/{session_key}` | 删除调试案例 |
| `POST /admin/files/upload` | 上传多模态附件到 OSS |

## 6. 开发流程

后端改动：

1. 修改 `router.py`/`schemas.py`/`service.py`/`repository.py`。
2. 如果只从 JSONB 派生字段，优先避免数据库迁移。
3. 新增字段后同步 `frontend/src/types.ts`。
4. 页面使用 `frontend/src/api/client.ts` 统一请求。

前端改动：

1. 在对应 `pages/*` 修改页面。
2. 公共状态放 `AdminShell`，公共展示组件放 `components/`。
3. 保持 Dashboard、Logs、Sessions 的筛选参数可以互相跳转。
4. 修改后运行 `npm run build`。

## 7. 验证命令

```bash
.venv/bin/python -m py_compile \
  src/main.py src/admin_api/router.py src/admin_api/schemas.py \
  src/admin_api/service.py src/observability/repository.py

PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py

cd frontend
npm run build
```

## 8. 清理原则

- 生产接口只维护 `/run`、`/stream_run`、`/cancel/{run_id}`、`/v1/chat/completions` 和 `/admin/*`。
- 旧节点级调试入口已移除，不再维护 `/node_run/{node_id}` 和 `/graph_parameter`。
- 旧 `src/workflows` 代码已删除，生产路由只保留主 Agent 链路。
- 架构说明只更新 `docs/AGENT_TECHNICAL_DOCUMENTATION.md`，避免多份文档冲突。
