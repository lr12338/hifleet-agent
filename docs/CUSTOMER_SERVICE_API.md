# HiFleet 客服 API 调用规范

本文面向内部开发、接入与运维人员，定义 `customer_support` 与 `customer_ceshi` 共用的 HTTP 调用契约。两条链路共用请求格式和接口地址，差异由 `agent_profile` 决定。

## 1. 接口与 Profile

| 接口 | 用途 | 推荐场景 |
| --- | --- | --- |
| `POST /run` | 同步执行 | 服务端转发、机器人回复、批量验证 |
| `POST /stream_run` | SSE 流式执行 | 需要逐步展示处理状态的 Web 客户端 |
| `POST /v1/chat/completions` | 兼容入口 | 仅供已有 OpenAI Chat Completions 调用方迁移使用 |

| `agent_profile` | 定位 | 说明 |
| --- | --- | --- |
| `customer_support` | 正式生产 | 面向客户的稳定客服链路 |
| `employee_assistant` | 兼容别名 | 会规范化为 `customer_support` |
| `customer_ceshi` | 测试与验证 | 受运行配置控制；实际 Provider、模式和回退以观测结果为准 |

Profile 选择优先级为：请求体 `agent_profile` → 请求头 `x-agent-profile` → 默认 `customer_support`。`source_channel` 只记录业务来源和观测信息，不参与路由。

## 2. 标准请求

请求必须包含至少一条 `role: "user"` 的 `messages`。推荐始终传入 `session_id`、`user_id`、`source_channel` 和显式的 `agent_profile`。

```json
{
  "messages": [
    {"role": "user", "content": "为什么船队轨迹看不到？"}
  ],
  "session_id": "websdk:tenant_a:user_100:conversation_001",
  "user_id": "user_100",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `messages` | 是 | 非空消息数组，至少包含一条用户消息 |
| `messages[].role` | 是 | 用户输入使用 `user` |
| `messages[].content` | 是 | 字符串，或包含文本/媒体段的数组 |
| `session_id` | 推荐 | 同一租户、用户和会话必须稳定复用；不同会话不可复用 |
| `user_id` | 推荐 | 用户标识，用于会话与后台筛选 |
| `source_channel` | 推荐 | 例如 `websdk`、`wechat_kf`、`crm` |
| `agent_profile` | 推荐 | 显式传 `customer_support` 或 `customer_ceshi` |
| `response_mode` | 否 | `/run` 可设为 `compact`，返回稳定的客户侧摘要结构 |

### 同步调用

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "请介绍 HiFleet 轨迹功能"}],
    "session_id": "websdk:tenant_a:user_100:conversation_001",
    "user_id": "user_100",
    "source_channel": "websdk",
    "agent_profile": "customer_support",
    "response_mode": "compact"
  }'
```

`response_mode=compact` 适合面向客户的稳定集成。响应至少包含：

```json
{
  "status": "success",
  "run_id": "...",
  "answer": "客户可见回复",
  "session_id": "websdk:tenant_a:user_100:conversation_001",
  "user_id": "user_100",
  "source_channel": "websdk",
  "agent_profile": "customer_support",
  "sources": [],
  "metrics": {}
}
```

`full` 是默认模式，包含更多运行结果，字段随运行时演进；调用方不要依赖内部 trace、工具调用或模型字段。请求校验失败返回 `400`；可重试的下游依赖错误在精简模式下可能返回 `503`。

## 3. 多模态消息

`content` 可为字符串，或使用下列数组段。媒体 URL 必须由调用方授权、可被服务访问，并应将媒体与本轮问题文字一并提交。

| 类型 | 结构 | 用途 |
| --- | --- | --- |
| `text` | `{"type":"text","text":"..."}` | 用户文字说明 |
| `image_url` | `{"type":"image_url","image_url":{"url":"https://..."}}` | 图片 |
| `input_audio` | `{"type":"input_audio","input_audio":{"url":"https://...","format":"amr"}}` | 语音；`format` 可选 |
| `video_url` | `{"type":"video_url","video_url":{"url":"https://..."}}` | 视频 |
| `file_url` | `{"type":"file_url","file_url":{"url":"https://..."}}` | 附件；不赋予文件系统或代码执行能力 |

### 图片 + 文字

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "image_url", "image_url": {"url": "https://files.example.test/ship-screen.png"}},
      {"type": "text", "text": "请说明截图中的船位异常。"}
    ]
  }],
  "session_id": "crm:tenant_a:user_100:case_001",
  "user_id": "user_100",
  "source_channel": "crm",
  "agent_profile": "customer_support"
}
```

### 语音 + 文字

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "input_audio", "input_audio": {"url": "https://files.example.test/question.amr", "format": "amr"}},
      {"type": "text", "text": "请根据语音内容协助查询。"}
    ]
  }],
  "session_id": "wechat_kf:hifleet:openid_example:default",
  "user_id": "openid_example",
  "source_channel": "wechat_kf",
  "agent_profile": "customer_support"
}
```

### 视频 + 文字

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "video_url", "video_url": {"url": "https://files.example.test/map-recording.mp4"}},
      {"type": "text", "text": "视频中的操作为什么没有生效？"}
    ]
  }],
  "session_id": "websdk:tenant_a:user_100:conversation_001",
  "user_id": "user_100",
  "source_channel": "websdk",
  "agent_profile": "customer_ceshi"
}
```

## 4. 微信兼容格式

微信旧调用方可以继续发送 `content.query.prompt`。服务端会映射 `text`、`image`、`voice`、`video` 为标准消息段；调用方仍应显式传入 `agent_profile`。

```json
{
  "content": {
    "query": {
      "prompt": [
        {"type": "image", "content": {"url": "https://files.example.test/map.png"}},
        {"type": "voice", "content": {"url": "https://files.example.test/question.amr", "format": "amr"}},
        {"type": "text", "content": {"text": "请结合图片和语音说明问题。"}}
      ]
    }
  },
  "session_id": "wechat_kf:hifleet:openid_example:default",
  "user_id": "openid_example",
  "source_channel": "wechat_kf",
  "agent_profile": "customer_support"
}
```

`location`、`link` 与 `event` 类型会被降级为文本。调用方不应把内部配置、密钥、日志或 Prompt 放入用户消息。

## 5. 流式调用

`/stream_run` 使用与 `/run` 相同的请求体，响应类型为 `text/event-stream`。每个 SSE 包使用 `event: message`，其 `data` 是 JSON 对象；客户端应按 JSON 内容安全展示最终客户回复，并将调试/处理中间事件视为可选信息。

```bash
curl -N -X POST http://127.0.0.1:10123/stream_run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "请查询船舶当前位置"}],
    "session_id": "websdk:tenant_a:user_100:conversation_001",
    "user_id": "user_100",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

客户端应处理断开、超时和非 `200` 响应；不得向客户展示 trace、隐藏推理、工具参数、密钥或环境变量。需要稳定的机器人文本回复时，优先使用 `/run` 的 `response_mode=compact`。

## 6. 两条链路的使用边界

### `customer_support`

用于正式生产客服。回复经过客户侧安全清洗，调用方也不得向客户透传内部运行字段。船舶写入和知识库维护必须满足该链路手册中的明确授权与字段完整性规则，详见 [CUSTOMER_SUPPORT.md](CUSTOMER_SUPPORT.md)。

### `customer_ceshi`

仅用于测试和验证。它当前可使用 Responses API 运行并在配置允许时回退到 Chat Function Calling，但这些属于运行时实现，不改变本页 HTTP 请求格式，也不是长期固定的对外承诺。

安全确认写入示例：先在隔离环境、同一稳定 `session_id` 下上传可识别媒体并收到“待确认”提示；仅在媒体候选仍有效、操作者已核对目标与数据后，再发送确认请求。不要在文档、自动化脚本或非隔离环境中填入实际 MMSI、坐标或时间。

```json
{
  "messages": [{"role": "user", "content": "确认按上一条媒体中的已核对数据执行更新。"}],
  "session_id": "test:isolated:user_100:media_update_001",
  "user_id": "test-user-100",
  "source_channel": "admin_panel",
  "agent_profile": "customer_ceshi"
}
```

若当前会话没有有效媒体候选、字段不完整或运行环境不允许写入，系统应拒绝执行或继续追问，而不是宣称更新成功。测试链路的内部模型、媒体转换和证据门禁见 [CUSTOMER_CESHI_ARCHITECTURE.md](CUSTOMER_CESHI_ARCHITECTURE.md)。

## 对话测试工作台与调试接口

### /run 与 /stream_run 用途区别

- `POST /run`：非流式，模拟外部服务真实调用。默认 `response_mode=compact` 模拟外部消费者；可切 `full`。保留原始 HTTP 状态码、响应体、`run_id`、总耗时。本地 E2E 必须真正请求 `http://127.0.0.1:10123/run`。
- `POST /stream_run`：SSE 流式，验证执行步骤、工具调用、输出过程和最终回复。普通流保持客户安全输出；管理台通过服务端内部调试 Token（`x-internal-debug-trace`，仅 `/admin/test/run` 代理注入）获取 DebugEvent V1 流。

### 两个 Profile 运行时区别

- `customer_support`：正式 Chat API/Chat Function Calling/LangGraph 客服链。
- `customer_ceshi`：Responses API 优先，能力不可用时回退到同链路 Chat Function Calling。当前 Provider 不支持原生 Responses token 流，V1 流为“步骤流”。

### 管理台测试代理契约

`/admin/test/run` 代理（`src/admin_api/service.py`）：
- **SSRF allowlist**：仅允许 `AGENT_BASE_URL` 与 `AGENT_ALLOWLIST` 配置地址；拒绝云元数据/链路本地地址。
- **分级超时**：connect/read/write/pool 分离，非单一总超时。
- **/run** 返回 upstream status、脱敏 headers、body、latency_ms、run_id。
- **/stream_run** 保留上游状态码/Content-Type/`x-run-id`；客户端断开关闭上游；空闲发 heartbeat；`finally` 记录 ended/cancelled/failed。
- **取消**：`POST /admin/test/cancel/{run_id}` 转发到 Agent `/cancel/{run_id}`。

### 请求/响应对外契约兼容

`/run` 与 `/stream_run` 的对外请求格式不变（共用 `messages`/`session_id`/`user_id`/`source_channel`/`agent_profile`），调试字段不进入普通客户响应。详见 `docs/DEBUG_EVENT_PROTOCOL.md` 与 `docs/ADMIN_CHAT_DEBUG.md`。
