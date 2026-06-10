# coze_ai（HiFleet 智能客服 Agent）

本项目是一个基于 `LangChain + LangGraph + FastAPI` 的多工具智能客服系统，核心目标是：

- 基于大模型处理用户问答（含微信消息格式与多模态输入）
- 按需调用业务工具（船舶查询/更新、知识检索）
- 统一通过 HTTP API 对外提供服务（`/run`、`/stream_run`、`/health` 等）

建议阅读入口：

- 主服务和对外接口：`README.md`
- Agent 管理平台全景：`docs/ADMIN_PLATFORM_DEVELOPER_GUIDE.md`
- 后台 API 与使用手册：`docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`
- 多用户接入规范：`docs/API_MULTI_USER_INTEGRATION.md`

---

## 1. 项目架构总览

### 1.1 核心分层

- `src/main.py`：服务入口，负责 HTTP 路由、请求适配、任务取消、SSE 流式输出
- `src/agents/agent.py`：主 Agent 构建，加载模型、系统提示词、工具集合
- `src/skills/*`：能力模块（每个 skill 包含 `SKILL.md` + `tools.py`）
- `config/agent_llm_config.json`：模型参数与工具清单
- `config/system_prompt_base.md`：基础系统提示词
- `src/utils/*`：会话状态、token 管理、数据库写入等基础能力

### 1.2 Skill 设计（按能力拆分）

- `knowledge_qa`：知识库/搜索能力（`smart_search`）
- `hifleet_ship_service`：船舶业务接口（查询、轨迹、PSC、上传等）

### 1.3 请求处理逻辑（简化）

1. 客户端调用 `POST /run` 或 `POST /stream_run`
2. `main.py` 做渠道适配（含微信格式 `content.query.prompt` 转标准输入）
3. `GraphService` 识别当前项目类型并获取 agent 实例
4. agent 执行：`system_prompt + tools + memory`
5. 若需要，自动调用 skill 工具
6. 返回最终回复（同步 JSON 或 SSE 流）

---

## 2. 目录说明（开发重点）

```text
coze_ai/
├── config/
│   ├── agent_llm_config.json      # 模型配置（模型名、温度等）
│   ├── system_prompt_base.md      # 基础系统提示词
│   └── system_prompt.md           # 历史/补充提示词
├── src/
│   ├── main.py                    # FastAPI 入口
│   ├── agents/agent.py            # Agent 构建逻辑
│   ├── skills/                    # 各业务能力模块
│   ├── tools/                     # 通用工具
│   └── utils/                     # token、session、db 等
├── scripts/
│   ├── http_run.sh                # HTTP 启动脚本
│   ├── local_run.sh               # 本地 flow/node 调试脚本
│   └── load_env.py                # 环境变量加载辅助
├── .env                           # 本地真实配置（不要提交）
└── .env.example                   # 环境变量模板
```

---

## 3. 环境变量与安全约定

### 3.1 必需变量（最小可运行）

```bash
COZE_WORKLOAD_IDENTITY_API_KEY=<你的模型/平台token>
COZE_INTEGRATION_MODEL_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
COZE_INTEGRATION_BASE_URL=https://api.coze.cn
```

### 3.2 常用业务变量

```bash
SHIP_SERVICE_API_URL=...
SHIP_SERVICE_API_TOKEN=...
ark_websearch_api_key=<火山联网搜索key>
```

联网搜索说明（`knowledge_qa/smart_search`）：

- 联网搜索使用火山联网搜索能力，读取 `ark_websearch_api_key`
- 该 key 可为当前业务发放的联网搜索 key（例如 `51GA...` 这种格式）
- **不要求**配置额外 `ark-` 形态的鉴权 key 才能启用联网搜索
- 若未配置该变量，`deep` 搜索会降级为仅知识库/站内结果

### 3.3 JWT OAuth（已迁移到环境变量）

`src/utils/coze_token_manager.py` 不再存放硬编码私钥，支持以下配置方式：

- `COZE_JWT_PUBLIC_KEY_ID` + `COZE_JWT_PRIVATE_KEY`（推荐）
- `COZE_KEY_1_* ... COZE_KEY_10_*`（多 key 轮询）
- `COZE_KEY_CONFIGS_JSON` / `COZE_KEY_CONFIGS_FILE`

> 安全要求：私钥只放 `.env` 或密钥系统，严禁写入代码仓库。

---

## 4. 快速启动（给新同学）

### 4.1 安装与初始化

```bash
cd /home/ecs-user/coze_ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

填写 `.env` 后，以系统服务方式启动（固定端口 `10123`）：

```bash
cd /home/ecs-user/coze_ai
bash scripts/install_systemd_service.sh
```

一体化本地启动（自动构建管理台 + 启动主服务）：

```bash
cd /home/ecs-user/coze_ai
bash scripts/start_unified_stack.sh
```

常用运维命令：

```bash
sudo systemctl status hifleet-agent.service
sudo systemctl restart hifleet-agent.service
sudo journalctl -u hifleet-agent.service -f
```

### 4.2 健康检查（固定端口 10123）

```bash
curl http://127.0.0.1:10123/health
```

期望返回：

```json
{"status":"ok","message":"Service is running"}
```

### 4.3 对外调用网络要求（跨服务器）

为了支持其他服务器调用本服务，请确保：

1. 服务监听 `0.0.0.0:10123`（已在 `src/main.py` 中固定 host 为 `0.0.0.0`）
2. 主机防火墙放通 TCP `10123`（如 `ufw` / `firewalld`）
3. 云厂商安全组/ACL 放通 TCP `10123`
4. 调用方使用 `http://<你的服务器IP>:10123`

### 4.4 使用 Nginx 启用 HTTPS（推荐）

已提供一键脚本，将 Nginx `443` 反向代理到本地 Agent 服务 `127.0.0.1:10123`：

```bash
cd /home/ecs-user/coze_ai

# 参数1：对外访问的域名或IP（默认 8.153.87.6）
bash scripts/install_nginx_https.sh 8.153.87.6
```

脚本默认读取证书（可通过环境变量覆盖）：

```bash
SSL_CERT_PATH=/etc/nginx/cert/_.hifleet.com_cert_chain.pem
SSL_KEY_PATH=/etc/nginx/cert/_.hifleet.com_key.key
```

验证：

```bash
# 若用 IP 调用且证书与 IP 不匹配，请临时加 -k
curl -k https://8.153.87.6/health
```

说明：

- 正式公网建议使用域名 + 匹配证书，避免证书告警
- Nginx 配置模板：`deploy/nginx/hifleet-agent-https.conf.template`
- 实际部署配置：`/etc/nginx/sites-enabled/hifleet-agent-https.conf`
- Nginx 统一使用系统服务托管（`nginx.service`）

Nginx 常用运维命令：

```bash
sudo systemctl status nginx
sudo systemctl restart nginx
sudo systemctl reload nginx
sudo systemctl enable nginx
sudo journalctl -u nginx -f
```

---

## 5. 对外接口调用文档（公网 10123）

多用户接入与会话隔离最佳实践请参考：`docs/API_MULTI_USER_INTEGRATION.md`

后台管理系统（统一骨架 / Dashboard / Sessions / Logs / Chat Debug / API Playground）请优先参考：

- `docs/ADMIN_PLATFORM_DEVELOPER_GUIDE.md`
- `docs/ADMIN_BACKEND_SYSTEM_GUIDE.md`

后台管理界面入口：`http://<服务器IP>:10123/admin-ui`

先定义统一访问地址（本机/远端都可复用）：

```bash
# HTTPS（推荐）
export BASE_URL="https://8.153.87.6"

# 若证书与IP不匹配，联调阶段可临时加 -k：
# curl -k -X POST ${BASE_URL}/run ...

# HTTP（保留）
# export BASE_URL="http://8.153.87.6:10123"
```

### 5.1 通用约定

- 协议：HTTP/HTTPS（公网推荐 HTTPS）
- 编码：`application/json; charset=utf-8`
- 超时：单次请求最长约 900 秒（服务端超时保护）
- 鉴权：当前示例为内网/白名单方式；如开放公网，建议在网关层增加签名/Token

### 5.2 核心接口清单

- `GET /health`：健康检查
- `POST /run`：同步问答（推荐）
- `POST /stream_run`：SSE 流式问答
- `POST /cancel/{run_id}`：取消执行任务
- `POST /v1/chat/completions`：OpenAI 兼容接口（按需开启）
- `GET /admin/logs`：后台日志列表（管理端）
- `GET /admin/logs/{run_id}`：后台调用详情（管理端）
- `GET /admin/dashboard/summary`：后台总览聚合（管理端）
- `GET /admin/sessions`：后台会话列表（管理端）
- `GET /admin/sessions/{session_id}`：后台会话时间线（管理端）
- `GET /admin/chat-debug/sessions`：调试会话列表（管理端）
- `PUT /admin/chat-debug/sessions/{session_key}`：调试会话持久化（管理端）
- `DELETE /admin/chat-debug/sessions/{session_key}`：删除调试会话（管理端）
- `POST /admin/test/run`：后台测试代理（管理端）
- `POST /admin/files/upload`：后台附件上传（管理端）

### 5.3 `POST /run`（主接口）

请求体（推荐）：

```json
{
  "messages": [
    {"role": "user", "content": "你好，请介绍一下你能做什么"}
  ],
  "session_id": "local_sess_001",
  "user_id": "local_user_001",
  "source_channel": "websdk"
}
```

字段说明：

- `messages`：对话消息数组，至少 1 条 user 消息
- `session_id`：会话 ID（建议业务侧唯一）
- `user_id`：用户 ID（建议与业务账号体系一致）
- `source_channel`：渠道标识（如 `websdk`、`wechat_mp`）

返回体（简化示例）：

```json
{
  "messages": [
    {"type": "human", "content": "用户输入"},
    {"type": "ai", "content": "模型回复"}
  ],
  "run_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

### 5.4 文本对话调用示例

> 若使用 IP 的 HTTPS 地址联调，请在 `curl` 命令中增加 `-k`（忽略证书域名校验）。

```bash
curl -X POST ${BASE_URL}/run \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"你好，请介绍一下你能做什么"}],
    "session_id": "local_sess_001",
    "user_id": "local_user_001",
    "source_channel": "websdk"
  }'
```

### 5.5 微信格式调用（兼容）

```bash
curl -X POST ${BASE_URL}/run \
  -H "Content-Type: application/json" \
  -d '{
    "content": {
      "query": {
        "prompt": [
          {
            "type": "image",
            "content": {
              "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/ark_demo_img_1.png"
            }
          },
          {
            "type": "text",
            "content": {
              "text": "支持输入图片的模型系列是哪个？"
            }
          }
        ]
      }
    },
    "session_id": "wx_mp_local_001",
    "user_id": "local_user_001",
    "source_channel": "wechat_mp"
  }'
```

### 5.6 `POST /stream_run`（SSE 流式）

```bash
curl -N -X POST ${BASE_URL}/stream_run \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}],"session_id":"s1","user_id":"u1","source_channel":"websdk"}'
```

### 5.7 多模态调用（图片/语音/视频）

#### 前置条件（必须满足）

1. 模型需支持对应输入模态。当前建议使用：

```json
"model": "doubao-seed-2-0-lite-260428"
```

2. `messages[].content` 需使用模型支持的 `type`：
- 图片：`image_url`
- 语音：`input_audio`
- 视频：`video_url`

3. 视频不支持 `input_video`，使用该类型会触发 400 错误。

4. 跨服务器调用时，服务端无法直接读取调用方本地文件路径（如 `/tmp/a.png`），文件需满足以下之一：
   - 可访问 URL（推荐：OSS/CDN/对象存储签名 URL）
   - Data URL（小文件联调可用，生产不建议大文件）

#### 5.7.1 传文件的推荐方式

- 生产推荐：先上传阿里云 OSS，再传 URL
- URL 要求：服务端可访问（公网或专线可达）
- 建议：使用短时签名 URL，避免长期裸链
- 大文件建议：控制时长/分辨率，避免超时

#### 图片识别示例

```bash
curl -X POST ${BASE_URL}/run \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "role":"user",
      "content":[
        {"type":"image_url","image_url":{"url":"https://ark-project.tos-cn-beijing.volces.com/doc_image/ark_demo_img_1.png"}},
        {"type":"text","text":"请分析图片内容"}
      ]
    }],
    "session_id":"mm_img_001",
    "user_id":"u1",
    "source_channel":"websdk"
  }'
```

#### 语音识别示例（URL）

```bash
curl -X POST ${BASE_URL}/run \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "role":"user",
      "content":[
        {"type":"input_audio","input_audio":{"url":"https://ark-project.tos-cn-beijing.volces.com/doc_audio/ark_demo_audio.mp3","format":"mp3"}},
        {"type":"text","text":"请识别音频中的内容"}
      ]
    }],
    "session_id":"mm_audio_001",
    "user_id":"u1",
    "source_channel":"websdk"
  }'
```

#### 视频识别示例（URL）

```bash
curl -X POST ${BASE_URL}/run \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "role":"user",
      "content":[
        {"type":"video_url","video_url":{"url":"https://example.com/demo.mp4"}},
        {"type":"text","text":"绿点是什么"}
      ]
    }],
    "session_id":"mm_video_001",
    "user_id":"u1",
    "source_channel":"websdk"
  }'
```

#### 5.7.2 已验证的 OSS URL 方式（示例）

以下模式已在本服务实测通过（`/run` + URL）：

- 图片 URL（`image_url.url`）可直接分析
- 音频 URL（`input_audio.url`，含 `amr`）可直接识别并触发后续工具
- 视频 URL（`video_url.url`）可直接分析

#### 本地资源测试建议

- 图片：`resources/images/exported_image.png`
- 语音：`resources/录音/测试船舶查询.amr`
- 视频：`resources/video/测试video.mp4`

本地文件建议先转为 data URL（`image_url.url`/`video_url.url`）或上传到可访问 URL 后再调用。

### 5.8 错误排查与状态码

常见 HTTP 状态：

- `200`：请求成功
- `400`：请求体格式错误（JSON 非法 / 字段类型不符）
- `500`：服务内部异常（可结合 `detail.error_code` 排查）

常见错误信息：

- `环境变量 COZE_INTEGRATION_BASE_URL 未设置`：缺少平台基础地址
- `audio input is not supported by this model`：模型不支持音频
- `invalid value: input_video`：视频类型错误，应改为 `video_url`
- `token is empty`：业务工具 token 未配置或映射错误

---

## 6. 开发与调试建议

### 6.1 常见问题定位

- `token is empty`：通常是 `SHIP_SERVICE_API_TOKEN` 未配置
- `401`：通常是数据库或平台写入鉴权未通过
- `COZE_INTEGRATION_BASE_URL 未设置`：知识库/搜索工具配置缺失
- 模型 `404/BadRequest`：检查 `COZE_WORKLOAD_IDENTITY_API_KEY`、`COZE_INTEGRATION_MODEL_BASE_URL`、模型名三者是否匹配
- `audio input is not supported by this model`：模型不支持 `input_audio`，需切换到支持音频的模型（如 `doubao-seed-2-0-lite-260428`）
- `invalid value: input_video`：视频类型写错，需使用 `video_url` 而不是 `input_video`

### 6.2 本地命令模式

```bash
# 直接跑一次 flow
bash scripts/local_run.sh -m flow

# 跑单节点
bash scripts/local_run.sh -m node -n node_name
```

### 6.3 修改能力时的建议流程

1. 改 `src/skills/<skill>/tools.py`（业务逻辑）
2. 更新对应 `SKILL.md`（提示词约束）
3. 通过 `/run` 做最小回归（至少 1 条成功 + 1 条失败场景）
4. 检查日志中 run_id 链路是否完整

---

## 7. 当前项目状态说明（给接手同学）

- 服务主链路可启动，`/health` 正常
- `/run` 可返回对话结果，工具是否成功取决于业务 token 完整性
- 兼容接口 `/v1/chat/completions` 仍建议单独回归后再对外开放

建议接手后第一步先完成：`.env` 全量校验 + 工具级联调。

