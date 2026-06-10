# HiFleet 智能客服技术文档（精简版）

## 1. 当前能力范围

本项目当前仅保留两类核心能力：

1. `knowledge_qa`：知识库与搜索问答（工具：`smart_search`）
2. `hifleet_ship_service`：船舶服务查询与更新（8 个船舶工具）

已移除能力：

- `lead_collection`
- `session_summary`
- `human_handoff`

## 2. 主链路架构

### 2.1 入口层

- `src/main.py`：FastAPI 入口，提供 `/run`、`/stream_run`、`/health`
- 微信请求会在入口层做格式适配

### 2.2 Agent 层

- `src/agents/agent.py`
  - 构建 LLM
  - 动态加载工具
  - 拼接系统提示词
  - 使用消息窗口裁剪历史对话

### 2.3 Skill 层

- `src/skills/knowledge_qa`：统一搜索工具 `smart_search`
- `src/skills/hifleet_ship_service`：船舶搜索、船位查询、档案、PSC、区域/海峡统计、上传与静态更新

### 2.4 配置层

- `config/system_prompt_base.md`：主系统规则
- `config/agent_llm_config.json`：模型参数与工具清单

## 3. 工具清单

### 3.1 搜索工具

- `smart_search`

### 3.2 船舶工具

- `ship_search`
- `get_ship_position`
- `get_ship_archive`
- `get_psc_records`
- `get_area_traffic`
- `get_strait_traffic`
- `upload_ship_position`
- `update_ship_static_info`

## 4. 运行与联调

### 4.1 启动

```bash
bash scripts/http_run.sh -p 5000
```

### 4.2 健康检查

```bash
curl http://127.0.0.1:5000/health
```

### 4.3 推荐请求格式

`/run` 与 `/stream_run` 推荐使用 `messages`：

```json
{
  "messages": [{"role": "user", "content": "查询育明位置"}],
  "session_id": "s1",
  "user_id": "u1",
  "source_channel": "websdk"
}
```

### 4.4 联网搜索配置（火山）

`knowledge_qa` 的 `smart_search` 在 `depth=deep` 时会触发联网搜索，配置要求如下：

```bash
ark_websearch_api_key=<你的火山联网搜索key>
```

说明：

- 联网搜索只依赖 `ark_websearch_api_key`
- 当前部署不需要额外配置 `ark-` 格式的 Ark 鉴权 key 来启用联网搜索
- 若该变量缺失，工具会自动降级，只返回知识库/站内可得结果

## 5. 多模态调用规范（已验证）

### 5.1 模型要求

多模态是否可用由模型能力决定。若要同时支持图片、语音、视频输入，建议配置：

```json
"model": "doubao-seed-2-0-lite-260428"
```

### 5.2 支持的 content 类型

- `text`
- `image_url`
- `input_audio`
- `video_url`
- `file`

注意：`input_video` 不是有效类型。

### 5.3 成功示例

#### 图片

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "image_url", "image_url": {"url": "https://.../demo.png"}},
      {"type": "text", "text": "请分析图片内容"}
    ]
  }]
}
```

#### 语音

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "input_audio", "input_audio": {"url": "https://.../demo.mp3", "format": "mp3"}},
      {"type": "text", "text": "请识别音频中的内容"}
    ]
  }]
}
```

#### 视频

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "video_url", "video_url": {"url": "https://.../demo.mp4"}},
      {"type": "text", "text": "绿点是什么"}
    ]
  }]
}
```

### 5.4 常见错误与修复

1. `audio input is not supported by this model`
   - 原因：模型不支持语音输入。
   - 处理：切换到支持 `input_audio` 的模型（如 `doubao-seed-2-0-lite-260428`）。

2. `invalid value: input_video`
   - 原因：视频类型字段错误。
   - 处理：将 `input_video` 改为 `video_url`。

3. 请求 500 但上游 400
   - 原因：服务层包装异常，根因在上游模型参数校验失败。
   - 处理：以日志中的上游错误为准修正 payload。

## 6. 验收要点

1. Agent 仅加载 `knowledge_qa` 与 `hifleet_ship_service`
2. 工具列表仅含 `smart_search` + 8 个船舶工具
3. 对话中不再触发线索收集、会话总结上传、转人工策略工具链路
