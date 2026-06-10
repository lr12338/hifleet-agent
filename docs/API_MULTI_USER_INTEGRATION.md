# 多用户接入与会话隔离指南

本文用于指导其他服务调用当前客服 API 时，保证：
- 每个用户会话彼此隔离
- 支持多轮上下文记忆
- 支持文本/图片/语音/视频输入

如需排查线上问题（串话、丢上下文、工具异常、慢请求），可配合后台管理系统使用：
- 系统全景：`docs/ADMIN_PLATFORM_DEVELOPER_GUIDE.md`
- 操作手册：`docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`
- 关键接口：`/admin/logs`、`/admin/logs/{run_id}`、`/admin/sessions`、`/admin/sessions/{session_id}`

## 1. 必填参数与语义

调用 `/run` 或 `/stream_run` 时，至少传入：

- `messages`: 对话消息数组（建议每次仅传当前轮 user 消息）
- `session_id`: 会话唯一标识（决定上下文线程）
- `user_id`: 用户唯一标识
- `source_channel`: 渠道标识（如 `websdk` / `wechat_mp` / 你方系统名）

说明：
- 服务端记忆主键使用 `session_id`。
- 同一会话必须复用同一个 `session_id`，否则会被当作新会话。
- 不同用户必须使用不同 `session_id`，避免串话。

## 2. session_id 生成规则（推荐）

推荐格式：

`{channel}:{tenant_id}:{user_id}:{conversation_id}`

示例：
- `crm:cn_hifleet:u_10086:c_20260610_0001`
- `wechat_mp:public:u_openid_xxx:c_default`

约束：
- 长度建议 <= 128
- 仅使用字母/数字/`-`/`_`/`:`
- 不要复用历史会话 ID 到新会话

## 3. 单轮调用示例（文本）

```json
{
  "messages": [
    {"role": "user", "content": "为什么轨迹查询没有反应？"}
  ],
  "session_id": "crm:tenant_a:u_10086:c_20260610_0001",
  "user_id": "u_10086",
  "source_channel": "crm"
}
```

## 4. 单轮调用示例（多模态）

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
  "source_channel": "crm"
}
```

语音/视频格式：
- 语音：`{"type":"input_audio","input_audio":{"url":"...","format":"mp3"}}`
- 视频：`{"type":"video_url","video_url":{"url":"..."}}`

## 5. 多轮上下文调用方式

调用方无需回传完整历史，可仅发送当前轮用户问题，但必须保持同一个 `session_id`。

示例：
- 第 1 轮：`session_id = crm:tenant_a:u_10086:c_1`，问“我查 LIN HAI 00330”
- 第 2 轮：同 `session_id`，问“上面我查的是哪艘船”
- 服务将基于同一会话记忆回复上下文

## 6. 并发与隔离建议

- 同一个 `session_id` 上的并发请求建议串行化，避免上下文竞争
- 不同 `session_id` 可并发
- 不要把 `session_id` 仅设置为 `user_id`（会混入多个业务会话）

## 7. 生产配置建议

为保证多 worker 下会话一致性，请启用共享持久化 checkpointer：

- 设置 `PGDATABASE_URL`
- 设置 `COZE_CHECKPOINTER_MODE=postgres`
- 设置 `COZE_HTTP_WORKERS=2~4`

未启用 Postgres 时，回退到进程内 MemorySaver，会在多 worker 场景出现会话记忆不一致。

## 8. 常见错误排查

- 现象：同用户多轮“记不住上文”
  - 排查：是否每轮更换了 `session_id`
  - 排查：是否开启多 worker 且仍在 MemorySaver 模式

- 现象：不同用户串话
  - 排查：是否复用了相同 `session_id`
  - 排查：网关层是否错误缓存了请求体

- 现象：用户反馈“回复慢/异常，但调用方侧无法定位”
  - 排查：通过 `/admin/logs` 按 `session_id` 或 `user_id` 检索目标请求
  - 排查：进入 `/admin/logs/{run_id}` 查看工具调用与错误明细

