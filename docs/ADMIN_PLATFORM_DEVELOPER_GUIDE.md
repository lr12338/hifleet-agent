# Agent 管理平台开发者总览

本文档面向接手 `coze_ai` 的开发同学，目标不是讲单个接口怎么调，而是帮助你在 10-15 分钟内建立对“当前系统长什么样、代码在哪、数据怎么流、改哪里最合适”的整体认知。

文档边界：

- 本文档负责“系统全景、代码地图、关键链路、改动入口”
- `docs/ADMIN_BACKEND_SYSTEM_GUIDE.md` 负责“怎么启动、怎么调接口、怎么用页面排障”
- 如果两份文档出现理解冲突，以本文档描述的当前系统结构为准，并回查代码

如果你是第一次接手这个仓库，建议阅读顺序：

1. 本文档：建立全景认知
2. `README.md`：补充主服务启动、环境变量、对外接口
3. `docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`：查后台 API 和页面使用细节
4. 具体代码文件：进入你要改的页面或链路

---

## 1. 当前系统是什么

当前仓库已经不是“只有一个调试页的 MVP”，而是一套运行在主 Agent 服务内部的商业化后台平台，包含两条主线：

- **对外 Agent 服务主线**：`/run`、`/stream_run`、`/health`
- **内部管理后台主线**：`/admin/*` API + `frontend/` 管理台 + `observability` 数据层

当前后台统一为 6 个一级页面：

- `总览`：Dashboard 总览、趋势、分布、高风险会话
- `会话`：最近活跃会话列表、消息回放、关联日志
- `调试`：Chat Debug 工作台，支持 SSE、多会话、附件、案例保存
- `日志追踪`：日志检索、结构化 trace、右侧详情抽屉
- `API 调试`：`/run` 与 `/stream_run` Playground
- `配置`：配置中心骨架页

这些页面共享同一套后台壳层、全局时间范围、右侧详情抽屉和通用状态组件。

---

## 2. 高层架构

### 2.1 运行时主链路

```text
Client
  -> /run or /stream_run
  -> src/main.py
  -> GraphService / Agent
  -> src/agents/agent.py
  -> src/skills/*/tools.py
  -> observability writer
  -> Postgres (observability schema)
```

主服务负责：

- 标准化请求体
- 兼容旧格式 `content.query.prompt`
- 处理同步 / SSE 流式输出
- 记录 `api_calls`
- 记录 `agent_errors`
- 记录 `tool_invocations`

### 2.2 后台平台链路

```text
frontend/src/App.tsx
  -> frontend/src/layouts/AdminShell.tsx
  -> frontend/src/pages/*
  -> frontend/src/api/client.ts
  -> /admin/*
  -> src/admin_api/router.py
  -> src/admin_api/service.py
  -> src/observability/repository.py
  -> Postgres (observability.*)
```

后台自身不单独起服务，直接挂在主 FastAPI 服务里，由 `/admin-ui` 提供构建后的前端静态资源。

### 2.3 最重要的 4 个链路键

理解后台时，一定先记住这几个键：

- `run_id`：一次请求/执行的唯一键，Logs 详情主键
- `session_id`：多轮会话键，Logs / Sessions / Chat Debug 的跨页联动键
- `user_id`：用户维度定位键
- `source_channel`：来源渠道键，比如 `websdk`、`wechat_mp`、`admin_panel`

---

## 3. 代码地图

### 3.1 后端关键目录

- `src/main.py`
  - 主服务入口
  - `/run`、`/stream_run`、`/health`
  - 观测写入初始化
  - `/admin-ui` 静态资源挂载

- `src/agents/agent.py`
  - 主 Agent 构建与意图分类
  - skill 组合和模型选择入口

- `src/admin_api/router.py`
  - 后台 HTTP 路由入口
  - 你要确认某个后台接口是否存在，先看这里

- `src/admin_api/service.py`
  - 后台接口业务层
  - 做聚合、摘要、预览字段转换、代理请求

- `src/observability/repository.py`
  - 观测数据读写核心
  - API 列表、日志详情、Dashboard 聚合、Sessions 列表都在这里查

- `src/observability/writer.py`
  - 异步写库入口
  - `schedule_api_call_log`
  - `schedule_tool_invocation_log`
  - `schedule_agent_error_log`

- `src/skills/common/tool_result.py`
  - 工具结果统一出口
  - 当前工具调用写库闭环就是从这里接入 observability 的

### 3.2 前端关键目录

- `frontend/src/App.tsx`
  - 路由入口
  - 当前页面结构一眼就能看出系统有哪些模块

- `frontend/src/layouts/AdminShell.tsx`
  - 全局 Header、Side Nav、全局时间范围、统一详情抽屉上下文

- `frontend/src/api/client.ts`
  - 所有后台请求统一入口
  - 如果你改后台 API，前端大多要同步改这里

- `frontend/src/types.ts`
  - Dashboard / Logs / Sessions 等页面复用的数据类型

- `frontend/src/pages/DashboardPage.tsx`
  - 运营总览页

- `frontend/src/pages/LogsPage.tsx`
  - 日志检索页
  - 使用统一抽屉展示 `run_id` 详情

- `frontend/src/pages/SessionPage.tsx`
  - 会话中心
  - 左侧列表、中间消息流、右侧元信息

- `frontend/src/pages/ChatDebugPage.tsx`
  - 最大、最复杂的页面
  - 也是当前最成熟的调试工作台

- `frontend/src/pages/TestPage.tsx`
  - API Playground
  - 支持同步和流式测试

---

## 4. 当前后台能力和对应接口

### 4.1 Dashboard 总览

前端页：

- `frontend/src/pages/DashboardPage.tsx`

后端接口：

- `GET /admin/dashboard/summary`

数据来源：

- `src/observability/repository.py::query_dashboard_summary()`

返回内容：

- `kpis`
- `trends`
- `distribution`
- `health`
- `risky_sessions`

适合改什么：

- KPI 口径
- 趋势粒度
- 渠道/路由排行逻辑
- 健康态字段扩展

### 4.2 Logs 日志追踪

前端页：

- `frontend/src/pages/LogsPage.tsx`
- `frontend/src/pages/LogDetailPage.tsx`

后端接口：

- `GET /admin/logs`
- `GET /admin/logs/{run_id}`

数据来源：

- `query_api_calls()`
- `query_log_detail()`

关键能力：

- 时间范围筛选
- 按 `session_id` / `user_id` / `route` / `status` / `keyword` 查询
- 右侧 Drawer 展示 request / response / tools / errors / trace

### 4.3 Sessions 会话中心

前端页：

- `frontend/src/pages/SessionPage.tsx`

后端接口：

- `GET /admin/sessions`
- `GET /admin/sessions/{session_id}`

数据来源：

- `query_session_summaries()`
- `query_session_calls()`

关键能力：

- 最近活跃会话发现
- 会话摘要
- 多轮请求回放
- 从会话跳转日志、调试页

### 4.4 Chat Debug 调试工作台

前端页：

- `frontend/src/pages/ChatDebugPage.tsx`

后端接口：

- `POST /admin/test/run`
- `GET /admin/chat-debug/sessions`
- `PUT /admin/chat-debug/sessions/{session_key}`
- `DELETE /admin/chat-debug/sessions/{session_key}`
- `POST /admin/files/upload`
- `POST /admin/ark/chat`

相关数据表：

- `observability.chat_debug_sessions`
- `observability.tool_invocations`
- `observability.api_calls`

关键能力：

- 多会话调试
- SSE 事件流渲染
- 思考 / 工具调用 / 回复结构化展示
- OSS 附件上传
- 调试会话持久化到 Postgres

### 4.5 Test Playground

前端页：

- `frontend/src/pages/TestPage.tsx`

后端接口：

- `POST /admin/test/run`

关键能力：

- 同步 `/run`
- 流式 `/stream_run`
- 最近请求历史
- 原始请求 / 响应查看

---

## 5. 数据模型与 Postgres 表

当前后台主要依赖 `observability` schema。

### 5.1 `observability.api_calls`

作用：

- 请求主表
- Dashboard、Logs、Sessions 的核心来源

重要字段：

- `run_id`
- `session_id`
- `user_id`
- `source_channel`
- `route`
- `status`
- `latency_ms`
- `request_json`
- `response_json`
- `created_at`

### 5.2 `observability.tool_invocations`

作用：

- 工具调用明细表
- Logs 详情、Chat Debug 工具区、Session 工具统计都依赖它

重要字段：

- `run_id`
- `tool_name`
- `tool_args`
- `tool_result`
- `status`
- `code`
- `message`
- `latency_ms`
- `source`
- `layer_trace`

当前写入闭环：

- 工具返回 `ToolResult`
- `emit_tool_metric()` 被调用
- `schedule_tool_invocation_log()` 异步写库

### 5.3 `observability.agent_errors`

作用：

- 运行异常记录
- Logs 详情错误页签的数据来源

### 5.4 `observability.chat_debug_sessions`

作用：

- Chat Debug 页面持久化
- 页面刷新后恢复调试会话

---

## 6. 两条最重要的数据闭环

### 6.1 请求观测闭环

```text
/run or /stream_run
  -> main.py
  -> _log_api_call_event()
  -> observability.writer.schedule_api_call_log()
  -> observability.api_calls
```

### 6.2 工具调用闭环

```text
skill tool
  -> ToolResult
  -> emit_tool_metric()
  -> observability.writer.schedule_tool_invocation_log()
  -> observability.tool_invocations
```

如果你发现 Logs/Chat Debug 里“工具链结构有了但没有数据”，优先检查这条链。

---

## 7. 页面跳转关系

这是后台平台化之后最常见的联动方式：

- `Dashboard -> Logs`
  - 看某个渠道、路由或异常会话的明细

- `Dashboard -> Sessions`
  - 打开高风险会话

- `Sessions -> Logs`
  - 通过 `session_id` 查看该会话所有调用

- `Sessions -> Chat Debug`
  - 继续在调试工作台复现某个 `session_id`

- `Logs -> Sessions`
  - 从单次调用反查整段会话

- `Logs -> Log Detail Drawer`
  - 查看 request / response / tool trace / errors

---

## 8. 开发时该从哪里下手

### 8.1 想改后台页面布局

优先看：

- `frontend/src/layouts/AdminShell.tsx`
- `frontend/src/components/page/*`
- `frontend/src/components/drawer/DetailDrawer.tsx`

不要直接在单页面里重复写 Header / 上下文条 / 抽屉逻辑。

### 8.2 想改后台接口返回字段

优先看：

- `src/admin_api/router.py`
- `src/admin_api/service.py`
- `src/observability/repository.py`

通常修改顺序：

1. repository 改 SQL / 聚合逻辑
2. service 做结构化输出
3. router 暴露参数
4. `frontend/src/api/client.ts` 和 `frontend/src/types.ts` 同步
5. 页面渲染适配

### 8.3 想补工具链数据

优先看：

- `src/skills/common/tool_result.py`
- `src/skills/*/tools.py`
- `src/observability/writer.py`

### 8.4 想排查 Chat Debug 行为异常

优先看：

- `frontend/src/pages/ChatDebugPage.tsx`
- `frontend/src/api/client.ts::consumeEventStream()`
- `src/admin_api/service.py::stream_test_run()`
- `src/main.py::http_stream_run`

---

## 9. 开发者常用验证清单

### 9.1 后端变更后

至少验证：

```bash
python3 -m py_compile src/main.py src/admin_api/router.py src/admin_api/service.py src/observability/repository.py
```

如果改了观测链路，建议再做一次真实调用，然后查：

```bash
curl "http://127.0.0.1:10123/admin/logs?page=1&page_size=1" -H "x-admin-api-key: ${ADMIN_API_KEY}"
curl "http://127.0.0.1:10123/admin/logs/<run_id>" -H "x-admin-api-key: ${ADMIN_API_KEY}"
```

### 9.2 前端变更后

```bash
cd frontend
npm run build
```

### 9.3 联调时最小验收

- Dashboard 能出 KPI 和趋势
- Logs 能筛选并打开详情抽屉
- Sessions 能看到最近活跃会话
- Chat Debug 刷新后还能恢复当前会话
- Test 页能打 `/run` 和 `/stream_run`
- 某次真实工具调用后，`tool_invocations` 非空

---

## 10. 当前已知情况与注意事项

### 10.1 Chat Debug 是当前最成熟页面

如果你要做组件抽象或体验升级，优先从 `ChatDebugPage` 提取可复用部分，再迁移到 Logs / Sessions。

### 10.2 后台是“嵌入主服务”的

不要把后台理解成独立 BFF。它和主 Agent 服务跑在同一个 FastAPI 进程里，共享：

- 路由服务
- 环境变量
- 数据库连接
- observability 数据

### 10.3 端口重启时要确认真正占用者

历史上这个项目既有 systemd 启动，也存在手动拉起 Python 进程的情况。  
如果你改了代码但重启后不生效，先确认实际监听 `10123` 的进程是谁，而不是默认假设只有 systemd 在管。

### 10.4 后续仍值得继续补强

当前系统已经可用，但以下方向仍是合理的下一步：

- `/admin/test/run` 的脱敏与能力收紧
- Dashboard 成本口径
- 更多 API 回归测试
- 更细粒度索引和分页摘要优化
- 页面按路由拆包，减小前端主 chunk

---

## 11. 推荐阅读入口

- 主服务说明：`README.md`
- 后台 API/使用指南：`docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`
- 多用户接入：`docs/API_MULTI_USER_INTEGRATION.md`
- 主服务入口：`src/main.py`
- 后台路由：`src/admin_api/router.py`
- 数据查询：`src/observability/repository.py`
- 前端路由：`frontend/src/App.tsx`

