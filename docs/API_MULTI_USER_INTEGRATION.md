# 多用户接入与会话隔离指南

本文指导外部服务或内部系统调用 HiFleet Agent API。文档入口见 `docs/README.md`，当前 Agent 架构见 `docs/AGENT_TECHNICAL_DOCUMENTATION.md`。

重点解决三件事：

- 多用户、多会话不串话。
- 正确选择客服或数字员工 Profile。
- 出现慢请求、错误、工具异常时可在后台管理系统定位。

注意：当前对外默认客服 profile 仍是 `customer_support`，但底层执行能力已经不再完全依赖单一路径：

- `customer_support` 仍负责外部客户收口、知识路由、Guard 和客服化输出
- `employee_assistant` 已能稳定承接纯文本 knowledge 主链，并继续负责文件/沙箱工作流

调用方不需要也不应该直接指定底层工具。

## 1. 核心接口

- `POST /run`：同步问答，推荐默认使用。
- `POST /stream_run`：SSE 流式问答，适合前端实时展示。
- `POST /cancel/{run_id}`：取消正在执行的请求。
- `GET /health`：健康检查。

## 2. 必填参数

调用 `/run` 或 `/stream_run` 时建议至少传入：

- `messages`：OpenAI 风格消息数组，通常只传当前轮用户消息。
- `session_id`：会话唯一标识，决定多轮上下文记忆。
- `user_id`：用户唯一标识。
- `source_channel`：来源渠道，用于 Profile 映射和后台筛选。
- `agent_profile`：可选但推荐显式传入，取值为 `customer_support` 或 `employee_assistant`。

兼容说明：`input`、`text`、`content.query.prompt` 仍可被服务端自动归一化为 `messages`，因此旧调用方可以平滑迁移，不需要一次性改完；但新接入和后续维护都应统一使用 `messages`。

Profile 解析优先级：请求体 `agent_profile` -> 请求头 `x-agent-profile` -> `source_channel` 映射 -> 默认 `customer_support`。

## 3. Profile 选择

| 场景 | 推荐 agent_profile | 推荐 source_channel | 说明 |
| --- | --- | --- | --- |
| 官网/产品内客服 | `customer_support` | `websdk` | 对客户友好回复，可检索公开信息和知识库 |
| 微信公众号/客服 | `customer_support` | `wechat_mp` / `wechat_kf` | 多轮客服问答 |
| CRM/工单系统 | `customer_support` | `crm` / `customer_api` | 外部客户支持场景 |
| 内部后台测试 | `employee_assistant` | `admin_panel` | 可测试内部工具和文件能力 |
| 内部员工助手 | `employee_assistant` | `employee_api` / `internal_web` | 需要内部鉴权和访问控制 |

安全要求：不要把 `employee_assistant` 暴露给未鉴权外部用户。该 Profile 可使用文件处理和受控 Python 分析能力。

`customer_support` 能力边界：

- 平台问题：当前主链优先 `local_kb_search -> web_search -> web_search_agent_browser`。
- 船舶问题：标准 Agent 可自主调用 ship service 工具，但最终回复仍受客服 Guard 约束。
- 写操作：只在 profile policy 允许且工具真实返回成功时才能对外宣称成功。
- 不向客户暴露 Python、Docker、内部路径、prompt、tool registry、日志和配置细节。

`employee_assistant` 当前能力边界：

- 纯文本知识问答可直接走三层知识链
- 文件/表格/Python/产物任务走 `plan -> act -> check -> loop`
- 更适合内部运营、测试、分析和后续统一执行骨架演进

## 4. session_id 生成规则

推荐格式：

```text
{channel}:{tenant_id}:{user_id}:{conversation_id}
```

示例：

- `websdk:tenant_a:u_10086:c_20260610_0001`
- `wechat_mp:hifleet:openid_xxx:c_default`
- `employee:finance:emp_001:quote_20260610`

约束：

- 同一会话必须复用同一个 `session_id`。
- 不同用户或不同业务会话不要复用 `session_id`。
- 同一个 `session_id` 上的并发请求建议串行化，避免上下文竞争。
- 长度建议不超过 128，字符建议使用字母、数字、`-`、`_`、`:`。

## 5. 客服调用示例

```json
{
  "messages": [
    {"role": "user", "content": "为什么轨迹查询没有反应？"}
  ],
  "session_id": "websdk:tenant_a:u_10086:c_20260610_0001",
  "user_id": "u_10086",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

多模态示例：

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        {"type": "text", "text": "这艘船为什么搜不到？"}
      ]
    }
  ],
  "session_id": "crm:tenant_a:u_10086:c_20260610_0001",
  "user_id": "u_10086",
  "source_channel": "crm",
  "agent_profile": "customer_support"
}
```

## 6. 数字员工调用示例

```json
{
  "messages": [
    {"role": "user", "content": "检查 /tmp/orders.xlsx，统计每个客户的报价总额。"}
  ],
  "session_id": "employee:finance:emp_001:quote_20260610",
  "user_id": "emp_001",
  "source_channel": "employee_api",
  "agent_profile": "employee_assistant"
}
```

文件处理约定：

- 文件路径必须位于工作目录、`/tmp` 或 `HIFLEET_AGENT_ARTIFACT_DIR` 下。
- Python 生成结果写入 `HIFLEET_AGENT_ARTIFACT_DIR`。
- 不要把敏感系统路径或未授权文件路径传给 Agent。

## 7. 流式调用

```bash
curl -N -X POST http://127.0.0.1:10123/stream_run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "请介绍HiFleet轨迹功能"}],
    "session_id": "websdk:u1:c1",
    "user_id": "u1",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

返回格式为 SSE：

```text
event: message
data: {...}
```

## 8. 生产配置建议

为保证多 worker 下会话一致性：

```bash
PGDATABASE_URL=postgresql://user:password@host:5432/postgres
COZE_CHECKPOINTER_MODE=postgres
COZE_HTTP_WORKERS=2
```

未启用 Postgres checkpointer 时会回退到进程内记忆，多 worker 场景可能出现同一会话上下文不一致。

## 9. 排障

后台入口：

```text
http://<server>:10123/admin-ui
```

常用后台接口：

- `GET /admin/logs?session_id=...`
- `GET /admin/logs?agent_profile=customer_support`
- `GET /admin/logs/{run_id}`
- `GET /admin/sessions?agent_profile=employee_assistant`
- `GET /admin/dashboard/summary`

常见问题：

| 现象 | 排查方向 |
| --- | --- |
| 多轮不记得上文 | 每轮是否更换了 `session_id`；多 worker 是否启用 Postgres checkpointer |
| 不同用户串话 | 是否复用了相同 `session_id`；网关是否错误缓存请求体 |
| 客服调用到了内部工具 | 是否误传 `agent_profile=employee_assistant` 或使用了内部 `source_channel` |
| 数字员工无法执行 Python | 是否使用 `employee_assistant`；路径是否在允许目录；代码是否触发安全规则 |
| 回复慢或异常 | 后台 Logs 按 `session_id`、`run_id`、`agent_profile` 查工具链和错误 |
