# Agent 后台管理系统使用指南

本文档聚焦“怎么用当前后台平台”，适合作为联调、验收、日常排障手册。  
如果你想先快速理解系统结构、代码地图和关键链路，请先看 `docs/ADMIN_PLATFORM_DEVELOPER_GUIDE.md`。

文档边界：

- 本文档只回答“怎么启动、怎么调用、怎么排障、页面怎么用”
- 不再重复展开系统架构和代码地图，避免与开发者总览形成双份解释
- 如果你要改代码，而不是使用后台，请回到 `docs/ADMIN_PLATFORM_DEVELOPER_GUIDE.md`

管理台统一访问入口：

- `http://127.0.0.1:10123/admin-ui`

---

## 1. 后台现在包含什么

当前后台已经是统一壳层的 Agent 管理平台，而不是早期单页 MVP。  
前端一级导航如下：

- `总览`
- `会话`
- `调试`
- `日志追踪`
- `API 调试`
- `配置`

其中：

- `总览` 对应 Dashboard 聚合视图
- `会话` 对应最近活跃会话中心
- `调试` 对应 Chat Debug 工作台
- `日志追踪` 对应 Logs 检索与 trace 详情
- `API 调试` 对应 `/run`、`/stream_run` Playground

---

## 2. 环境准备

### 2.1 必需项

- 主 Agent 服务可启动
- 可访问的 Postgres
- Python / Node 依赖已安装

### 2.2 关键环境变量

```bash
PGDATABASE_URL=postgresql://user:password@127.0.0.1:5432/postgres
COZE_CHECKPOINTER_MODE=postgres
COZE_HTTP_WORKERS=4

# 配置后，所有 /admin/* 请求需要 x-admin-api-key
ADMIN_API_KEY=your_admin_secret

# 后台测试代理默认回源到主服务
AGENT_BASE_URL=http://127.0.0.1:10123

# Chat Debug 附件上传依赖
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
OSS_BUCKET_NAME=...
OSS_ENDPOINT=...
OSS_REGION=cn-beijing
```

说明：

- 后台观测数据使用 `observability` schema
- 会话记忆使用 `memory` schema
- Chat Debug 会话持久化也落在 `observability` 下

---

## 3. 启动方式

### 3.1 一体化启动

```bash
cd /home/ecs-user/coze_ai
source .venv/bin/activate
bash scripts/start_unified_stack.sh
```

特点：

- 自动构建 `frontend/dist`
- 主服务和后台共用一个端口 `10123`
- 打开 `http://127.0.0.1:10123/admin-ui` 即可访问后台

### 3.2 前端本地开发

```bash
cd /home/ecs-user/coze_ai/frontend
npm install
npm run dev
```

默认地址：

- `http://127.0.0.1:5173/admin-ui`

### 3.3 启动后的初始化

服务启动时会自动初始化：

- `src/observability/sql/001_init_observability.sql`
- `src/observability/sql/002_init_chat_debug_sessions.sql`

---

## 4. 后台 API 总览

统一前缀：

- `/admin`

若设置了 `ADMIN_API_KEY`，所有请求都要带：

```bash
-H "x-admin-api-key: ${ADMIN_API_KEY}"
```

### 4.1 Dashboard

`GET /admin/dashboard/summary`

用途：

- 总调用量
- 会话量
- 成功率
- 平均延迟
- 趋势图
- 分布排行
- 高风险会话

示例：

```bash
curl "http://127.0.0.1:10123/admin/dashboard/summary" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

### 4.2 Logs

#### 日志列表

`GET /admin/logs`

支持参数：

- `page`、`page_size`
- `start_time`、`end_time`
- `session_id`
- `user_id`
- `source_channel`
- `route`
- `status`
- `keyword`

示例：

```bash
curl "http://127.0.0.1:10123/admin/logs?page=1&page_size=20&status=error&keyword=psc" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

#### 单次调用详情

`GET /admin/logs/{run_id}`

返回主要字段：

- `api_call`
- `tool_invocations`
- `errors`
- `summary`
- `trace`

示例：

```bash
curl "http://127.0.0.1:10123/admin/logs/<run_id>" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

### 4.3 Sessions

#### 会话列表

`GET /admin/sessions`

支持参数：

- `start_time`、`end_time`
- `user_id`
- `source_channel`
- `status`
- `keyword`
- `page`、`page_size`

#### 会话详情

`GET /admin/sessions/{session_id}`

返回：

- `session_id`
- `user_id`
- `source_channel`
- `summary`
- `calls`

示例：

```bash
curl "http://127.0.0.1:10123/admin/sessions?page=1&page_size=10" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"

curl "http://127.0.0.1:10123/admin/sessions/<session_id>" \
  -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

### 4.4 Chat Debug 会话持久化

- `GET /admin/chat-debug/sessions`
- `PUT /admin/chat-debug/sessions/{session_key}`
- `DELETE /admin/chat-debug/sessions/{session_key}`

用途：

- 保存调试案例
- 刷新后恢复当前调试会话
- 删除历史调试会话

### 4.5 Test Playground / 调试代理

`POST /admin/test/run`

支持：

- 代理 `/run`
- 代理 `/stream_run`

同步示例：

```json
{
  "endpoint": "/run",
  "payload": {
    "messages": [{"role": "user", "content": "你好"}],
    "session_id": "admin_test_s1",
    "user_id": "admin_user",
    "source_channel": "admin_panel"
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
    "source_channel": "admin_panel"
  }
}
```

### 4.6 文件上传

`POST /admin/files/upload`

用途：

- Chat Debug 上传图片 / 音频 / 视频到 OSS
- 返回可直接用于多模态推理的 URL

---

## 5. 前端页面使用

### 5.1 登录

- 配置了 `ADMIN_API_KEY`：在登录页输入 API Key
- 未配置 `ADMIN_API_KEY`：可免密进入

### 5.2 Dashboard

用于看：

- KPI
- 调用趋势
- 热门渠道 / 路由
- 高风险会话
- 服务健康态

### 5.3 Logs

适合：

- 按 `run_id` / `session_id` / 关键词排障
- 打开右侧详情抽屉看 request / response / tools / errors / trace

### 5.4 Sessions

适合：

- 查最近活跃会话
- 回放多轮消息
- 从会话跳到 Logs 或 Chat Debug

### 5.5 Chat Debug

适合：

- 复现某个问题会话
- 查看流式思考和工具调用
- 上传 OSS 附件做多模态调试
- 保存案例、分享链接、导出记录

### 5.6 API 调试

适合：

- 构造 `/run` 或 `/stream_run` 请求
- 看流式事件
- 保存最近请求模板

---

## 6. 当前观测表

### 6.1 `observability.api_calls`

主请求表，Dashboard / Logs / Sessions 都依赖它。

### 6.2 `observability.tool_invocations`

工具调用明细表，Logs / Chat Debug / Sessions 的工具数据都依赖它。

### 6.3 `observability.agent_errors`

异常表，用于错误定位。

### 6.4 `observability.chat_debug_sessions`

调试工作台会话持久化表。

最重要的关联键：

- `run_id`
- `session_id`
- `user_id`
- `source_channel`

---

## 7. 常见排障

### 7.1 `/admin/logs` 没数据

优先检查：

1. 是否真的调用过 `/run` 或 `/stream_run`
2. `PGDATABASE_URL` 是否可连
3. 服务日志里是否有 `[Observability]` 告警
4. `observability` schema 是否存在

### 7.2 Logs 有调用，但没有工具链

优先检查：

1. 对应 skill 是否最终调用了 `emit_tool_metric()`
2. `tool_result.py` 是否成功走到 `schedule_tool_invocation_log()`
3. `observability.tool_invocations` 是否有新纪录

### 7.3 Chat Debug 刷新后不恢复

优先检查：

1. `observability.chat_debug_sessions` 是否存在
2. 后台接口 `/admin/chat-debug/sessions` 是否 200
3. 前端是否拿到了 `session_key` 和 `payload`

### 7.4 后台接口 401

- 检查 `ADMIN_API_KEY`
- 检查请求头 `x-admin-api-key`

### 7.5 改代码后重启似乎没生效

这个项目历史上既有 systemd 启动，也可能有手动拉起的 Python 进程。  
如果发现“明明重启了但代码没生效”，先确认谁真正监听了 `10123`。

---

## 8. 推荐开发回归

每次改后台相关功能，建议至少做这几步：

```bash
python3 -m py_compile src/admin_api/router.py src/admin_api/service.py src/observability/repository.py

cd frontend
npm run build
```

再做一次最小联调：

1. 打开 Dashboard，确认 summary 正常
2. 打开 Logs，确认能筛选和拉起详情
3. 打开 Sessions，确认有列表
4. 用 Chat Debug 发起一次真实请求
5. 确认对应 `run_id` 的 `tool_invocations` 非空


