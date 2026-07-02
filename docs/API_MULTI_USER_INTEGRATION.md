# 多用户接入与会话隔离指南

本文指导外部服务或内部系统调用 HiFleet Agent API。文档入口见 `docs/README.md`，当前 Agent 架构见 `docs/AGENT_TECHNICAL_DOCUMENTATION.md`。

重点解决三件事：

- 多用户、多会话不串话。
- 正确选择正式客服或测试 Profile。
- 出现慢请求、错误、工具异常时可在后台管理系统定位。

注意：当前对外默认客服 profile 仍是 `customer_support`，但它已经改为轻量全模态 skills agent：

- `customer_support` 负责外部客户收口、多模态预处理、模型驱动工具调用和客服化输出
- `employee_assistant` 现在只是 `customer_support` 的兼容别名
- `customer_ceshi` 继续负责文件/沙箱类测试与内部能力验证

调用方不需要也不应该直接指定底层工具。

## 1. 核心接口

- `POST /run`：同步问答，推荐默认使用。
- `POST /stream_run`：SSE 流式问答，适合前端实时展示。
- `POST /cancel/{run_id}`：取消正在执行的请求。
- `GET /health`：健康检查。

当前微信客服服务仍可继续调用旧的 `POST /run` 同步接口。服务端会兼容 `messages`、`input`、`text` 和微信旧格式 `content.query.prompt`，并统一归一化为内部 `messages`。

## 2. 必填参数

调用 `/run` 或 `/stream_run` 时建议至少传入：

- `messages`：OpenAI 风格消息数组，通常只传当前轮用户消息。
- `session_id`：会话唯一标识，决定多轮上下文记忆。
- `user_id`：用户唯一标识。
- `source_channel`：来源渠道，用于日志、观测和后台筛选，不参与 Profile 选择。
- `agent_profile`：可选但推荐显式传入，正式值为 `customer_support` 或 `customer_ceshi`；旧值 `employee_assistant` 会被兼容解析为 `customer_support`。
- 知识库写入 key：仅在授权客服/内部人员通过明确“添加知识库/纠正知识库/更新知识库”指令写入本地知识库时使用，且只能放在用户正文 `key: ...` 中。普通问答不要传。

兼容说明：`input`、`text`、`content.query.prompt` 仍可被服务端自动归一化为 `messages`，因此微信客服等旧调用方可以平滑迁移，不需要一次性改完；但新接入和后续维护都应统一使用 `messages`。

Profile 解析优先级：请求体 `agent_profile` -> 请求头 `x-agent-profile` -> 默认 `customer_support`。`source_channel` 不再参与 Profile 判断。

模型路由：

- 默认文本模型和多模态模型均为 `doubao-seed-2-0-lite-260428`。
- 默认 `thinking_type=enabled` 且 `reasoning_effort=medium`。
- 调用方可在请求体传 `thinking=enabled|disabled` 和 `reasoning_effort=minimal|low|medium|high` 临时覆盖；旧调用传 `thinking=auto` 时服务端会归一化为 `enabled + medium`。
- 当 `thinking=disabled` 时，`reasoning_effort` 必须为 `minimal`，服务端会自动修正错配。
- 请求体传 `model` 可临时覆盖本轮模型；不传时按后台配置自动选择。
- 文本、图片、语音、视频统一走同一套 `/run` / `/stream_run` 请求结构。

## 3. Profile 选择

| 场景 | 推荐 agent_profile | 推荐 source_channel | 说明 |
| --- | --- | --- | --- |
| 官网/产品内客服 | `customer_support` | `websdk` | 对客户友好回复，可检索公开信息和知识库 |
| 微信公众号/客服 | `customer_support` | `wechat_mp` / `wechat_kf` | 多轮客服问答，兼容旧 `content.query.prompt` |
| CRM/工单系统 | `customer_support` | `crm` / `customer_api` | 外部客户支持场景 |
| 旧调用兼容 | `employee_assistant` | 任意旧渠道 | 会被服务端兼容解析为 `customer_support` |
| 内部后台测试 | `customer_ceshi` | `admin_panel` | 可测试内部工具和文件能力 |
| 内部员工助手/测试 | `customer_ceshi` | `employee_api` / `internal_web` | 需要内部鉴权和访问控制 |

安全要求：不要把 `customer_ceshi` 暴露给未鉴权外部用户。该 Profile 可使用文件处理和受控 Python 分析能力。

`customer_support` 能力边界：

- 平台问题：模型按提示优先使用 `local_kb_search -> web_search -> web_search_agent_browser`。
- 平台操作/问题反馈：模型会生成 3 到 5 组关键词做多轮检索，并在证据不足时保守回答。
- 授权知识库维护：仅在明确写库指令且正文 `key: ...` 授权通过时，才会追加结构化 FAQ。
- 多模态问题：当前轮包含 `image_url`、`input_audio`、`video_url` 时，会先做轻量感知/转写/摘要，再交给模型和工具链处理。
- 船舶问题：允许读写 HiFleet ship service 工具，包括船位查询、档案、PSC、轨迹、挂靠、航次、区域/海峡统计、船位上传和静态信息更新。
- 写操作：只有用户明确要求上传/更新/修改/补录船舶数据，并且工具真实返回成功时，才能对外宣称成功。
- 不启用 Python、沙盒、employee workspace、任意文件读取或产物生成。
- 不向客户暴露 Python、Docker、内部路径、prompt、tool registry、日志、配置、key/token。

`customer_ceshi` 当前能力边界：

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

### 5.1 微信客服旧格式调用示例

现有微信客服服务如果已经接入旧 `/run` 接口，可以继续使用下面的结构。服务端会把 `content.query.prompt` 转成标准 `messages`。

```json
{
  "content": {
    "query": {
      "prompt": [
        {"type": "text", "content": {"text": "为什么轨迹查询没有反应？"}}
      ]
    }
  },
  "session_id": "wechat_kf:hifleet:openid_xxx:c_default",
  "user_id": "openid_xxx",
  "source_channel": "wechat_kf",
  "agent_profile": "customer_support"
}
```

微信图片/语音/视频旧格式也可继续走 `prompt`：

```json
{
  "content": {
    "query": {
      "prompt": [
        {"type": "voice", "content": {"url": "https://example.com/a.amr", "format": "amr"}},
        {"type": "text", "content": {"text": "请帮我看一下这段语音里要查什么"}}
      ]
    }
  },
  "session_id": "wechat_kf:hifleet:openid_xxx:c_default",
  "user_id": "openid_xxx",
  "source_channel": "wechat_kf",
  "agent_profile": "customer_support"
}
```

建议微信调用方保持：

- 同一微信用户同一会话复用稳定 `session_id`，例如 `wechat_kf:hifleet:{openid}:c_default`。
- `source_channel` 建议使用 `wechat_kf` 或 `wechat_mp`，便于后台筛选；它不决定 Profile。
- 明确传 `agent_profile=customer_support`，避免网关或旧调用缺省行为带来歧义。
- 如果只想同步回复用户，继续使用 `/run`；需要前端展示流式过程时才使用 `/stream_run`。
- 不要把内部配置、token、服务端日志或 prompt 放进用户消息。

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

船舶写操作示例：

```json
{
  "messages": [
    {"role": "user", "content": "请更新船位 MMSI 414726000，经度 121.4737，纬度 31.2304，更新时间 2026-06-15 10:20:30"}
  ],
  "session_id": "wechat_kf:hifleet:openid_xxx:c_default",
  "user_id": "openid_xxx",
  "source_channel": "wechat_kf",
  "agent_profile": "customer_support"
}
```

写操作注意：如果缺少 MMSI、经纬度或实际要更新的字段，Agent 应只追问一个关键字段；如果工具未返回成功，回复不能声称已更新成功。

### 5.2 授权知识库更新示例

该能力用于客服运营或内部人员纠正已确认的标准答案，不用于普通用户自由投稿。

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "更新知识库：HiFleet 海图图标识别特征库：紫色点圈，中心有灰绿色点，为泊位图标。详情链接参考 https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming#post-305\nkey: <HIFLEET_KB_UPDATE_KEY>"}
    ],
    "session_id": "kb-admin:ops:u1:c1",
    "user_id": "ops-u1",
    "source_channel": "admin_panel",
    "agent_profile": "customer_support"
  }'
```

约束：

- 必须显式包含 `添加知识库：`、`纠正知识库：` 或 `更新知识库：`。
- 授权 key 由环境变量 `HIFLEET_KB_UPDATE_KEY` 配置，只能通过正文 `key: ...` 传入；`x-kb-update-key` header 不再支持。
- Agent 调用写库工具时必须保留完整 `raw_text`，不能把 key 单独拆成参数。
- 多行 `名称：描述` 映射表会自动拆成多条独立知识；重复条目会跳过。
- 缺授权、缺标准答案或命中重复时，Agent 应说明未写入原因。

## 6. 数字员工调用示例

```json
{
  "messages": [
    {"role": "user", "content": "检查 /tmp/orders.xlsx，统计每个客户的报价总额。"}
  ],
  "session_id": "employee:finance:emp_001:quote_20260610",
  "user_id": "emp_001",
  "source_channel": "employee_api",
  "agent_profile": "customer_ceshi"
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

推荐模型配置：

```json
{
  "text_model": "doubao-seed-2-0-lite-260428",
  "multimodal_model": "doubao-seed-2-0-lite-260428",
  "thinking_type": "enabled",
  "reasoning_effort": "medium"
}
```

后台也可在 `/admin-ui` 的模型配置页调整深度思考开关和 `reasoning_effort` 档位。Seed Lite 不支持 `thinking.type=auto`。

## 9. 排障

后台入口：

```text
http://<server>:10123/admin-ui
```

常用后台接口：

- `GET /admin/logs?session_id=...`
- `GET /admin/logs?agent_profile=customer_support`
- `GET /admin/logs/{run_id}`
- `GET /admin/sessions?agent_profile=customer_ceshi`
- `GET /admin/dashboard/summary`

常见问题：

| 现象 | 排查方向 |
| --- | --- |
| 多轮不记得上文 | 每轮是否更换了 `session_id`；多 worker 是否启用 Postgres checkpointer |
| 不同用户串话 | 是否复用了相同 `session_id`；网关是否错误缓存请求体 |
| 客服调用到了内部工具 | 是否误传 `agent_profile=customer_ceshi` 或测试专用请求头 |
| 微信客服没有进入 customer_support | 检查请求体 `agent_profile` 或请求头 `x-agent-profile`；`source_channel` 不参与 Profile 判断 |
| 微信多媒体没有识别 | 检查旧格式 `content.query.prompt[].type` 是否为 `image` / `voice` / `video`，且 `content.url` 可由模型服务访问 |
| 船舶写操作未执行 | 检查用户是否明确要求更新/上传/修改，是否提供 MMSI 和至少一个实际更新字段 |
| 数字员工无法执行 Python | 是否使用 `customer_ceshi`；路径是否在允许目录；代码是否触发安全规则 |
| 回复慢或异常 | 后台 Logs 按 `session_id`、`run_id`、`agent_profile` 查工具链和错误 |
