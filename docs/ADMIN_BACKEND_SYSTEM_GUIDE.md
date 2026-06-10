# Agent 后台管理系统使用指南

本文面向联调、验收和日常排障。系统架构和代码地图见 `docs/AGENT_TECHNICAL_DOCUMENTATION.md`。

后台入口：

```text
http://127.0.0.1:10123/admin-ui
```

如果配置了 `ADMIN_API_KEY`，所有 `/admin/*` 请求需要请求头：

```bash
-H "x-admin-api-key: ${ADMIN_API_KEY}"
```

## 1. 页面能力

| 页面 | 用途 |
| --- | --- |
| 总览 Dashboard | 查看调用量、成功率、延迟、工具成功率、渠道/路由/Profile 分布、高风险会话 |
| 会话 Sessions | 按时间、关键词、Profile 浏览会话，回放消息并跳转日志 |
| 调试 Chat Debug | 多会话对话调试，支持附件、SSE、显式 `agent_profile`、案例保存 |
| 日志追踪 Logs | 按 run_id/session_id/user_id/source_channel/agent_profile/status/keyword 检索 |
| API 调试 Playground | 构造 `/run` 和 `/stream_run` 请求，可覆盖 Profile、渠道、模型等参数 |
| 配置 Config | 配置中心骨架页，后续可接入 Profile/Skill 可视化管理 |

## 2. Dashboard

接口：

```text
GET /admin/dashboard/summary
```

展示内容：

- KPI：请求数、会话数、成功率、平均延迟、工具成功率、估算成本。
- 趋势：按小时聚合请求、错误、平均延迟。
- 分布：热门渠道、热门路由、Agent Profile。
- 高风险会话：按错误数、延迟和最近活跃排序。

示例：

```bash
curl "http://127.0.0.1:10123/admin/dashboard/summary" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

## 3. Logs

接口：

```text
GET /admin/logs
GET /admin/logs/{run_id}
```

列表参数：

- `page`、`page_size`
- `start_time`、`end_time`
- `session_id`
- `user_id`
- `source_channel`
- `agent_profile`
- `route`
- `status`
- `keyword`

示例：

```bash
curl "http://127.0.0.1:10123/admin/logs?page=1&page_size=20&agent_profile=customer_support&status=error" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

详情页包含：

- `api_call`：请求主记录，包含派生字段 `agent_profile`。
- `tool_invocations`：工具调用链。
- `errors`：错误明细。
- `summary`：摘要字段。
- `trace`：请求、工具、错误、响应时间线。

## 4. Sessions

接口：

```text
GET /admin/sessions
GET /admin/sessions/{session_id}
```

列表参数：

- `start_time`、`end_time`
- `user_id`
- `source_channel`
- `agent_profile`
- `status`
- `keyword`
- `page`、`page_size`

示例：

```bash
curl "http://127.0.0.1:10123/admin/sessions?agent_profile=employee_assistant&page=1&page_size=10" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

会话详情返回：

- `session_id`
- `user_id`
- `source_channel`
- `agent_profile`
- `summary`
- `calls`

## 5. Chat Debug

用途：

- 模拟客服或数字员工对话。
- 在高级参数中设置 `session_id`、`user_id`、`source_channel`、`agent_profile`。
- 上传图片、音频、视频附件到 OSS。
- 保存调试案例，刷新后恢复。

相关接口：

- `POST /admin/test/run`
- `GET /admin/chat-debug/sessions`
- `PUT /admin/chat-debug/sessions/{session_key}`
- `DELETE /admin/chat-debug/sessions/{session_key}`
- `POST /admin/files/upload`

## 6. API Playground

接口：

```text
POST /admin/test/run
```

同步示例：

```json
{
  "endpoint": "/run",
  "payload": {
    "messages": [{"role": "user", "content": "你好"}],
    "session_id": "admin_test_s1",
    "user_id": "admin_user",
    "source_channel": "admin_panel",
    "agent_profile": "employee_assistant"
  }
}
```

流式示例：

```json
{
  "endpoint": "/stream_run",
  "stream": true,
  "payload": {
    "messages": [{"role": "user", "content": "请流式回答"}],
    "session_id": "admin_stream_s1",
    "user_id": "admin_user",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }
}
```

## 7. 启动与依赖

一体化启动：

```bash
cd /home/ecs-user/coze_ai
source .venv/bin/activate
bash scripts/start_unified_stack.sh
```

前端本地开发：

```bash
cd /home/ecs-user/coze_ai/frontend
npm install
npm run dev
```

关键环境变量：

```bash
PGDATABASE_URL=postgresql://user:password@127.0.0.1:5432/postgres
COZE_CHECKPOINTER_MODE=postgres
ADMIN_API_KEY=your_admin_secret
AGENT_BASE_URL=http://127.0.0.1:10123
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
OSS_BUCKET_NAME=...
OSS_ENDPOINT=...
OSS_REGION=cn-beijing
```

## 8. 排障路径

1. 先在 Dashboard 看是否集中在某个 Profile、渠道或路由。
2. 到 Logs 按 `session_id`、`user_id`、`agent_profile` 筛选。
3. 打开日志详情查看 request、response、tools、errors、trace。
4. 到 Sessions 回放完整上下文，判断是否是会话串扰或用户表达不清。
5. 用 Chat Debug 或 API Playground 复现，并保存案例。
