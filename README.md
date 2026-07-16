# HiFleet `customer_support` 服务

HiFleet 企业客服生产服务。正式客服链路使用 Chat Completions API 运行时，通过 FastAPI 在端口 `10123` 提供同步、流式和 OpenAI 兼容接口。

生产链路的唯一完整说明见 [docs/CUSTOMER_SUPPORT.md](docs/CUSTOMER_SUPPORT.md)。测试中的 `customer_ceshi` / Responses API 链路不属于本说明范围。

## 快速启动

```bash
cd /home/ecs-user/coze_ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 按部署环境填写 .env
bash scripts/start_unified_stack.sh
```

健康检查：

```bash
curl http://127.0.0.1:10123/health
```

后台管理台：`http://127.0.0.1:10123/admin-ui`

使用 systemd 部署时：

```bash
bash scripts/install_systemd_service.sh
sudo systemctl restart hifleet-agent.service
sudo journalctl -u hifleet-agent.service -f
```

## 生产 Profile

- 默认 Profile 是 `customer_support`，面向 `websdk`、`wechat_mp`、`wechat_kf`、`customer_api` 和 `crm`。
- `employee_assistant` 是 `customer_support` 的兼容别名，运行时会规范化为 `customer_support`。
- 选择优先级：请求体 `agent_profile` → 请求头 `x-agent-profile` → 默认 `customer_support`。
- `source_channel` 仅用于会话、日志与观测，不决定 Profile。

## 必需配置

按实际部署配置模型、Coze 鉴权、持久化记忆、船舶服务和联网检索；不要将密钥写入请求或文档。

```bash
COZE_WORKLOAD_IDENTITY_API_KEY=...
COZE_INTEGRATION_MODEL_BASE_URL=...
COZE_INTEGRATION_BASE_URL=...
PGDATABASE_URL=...
COZE_CHECKPOINTER_MODE=postgres
SHIP_SERVICE_API_URL=...
SHIP_SERVICE_API_TOKEN=...
ark_websearch_api_key=...
HIFLEET_KB_UPDATE_KEY=...
```

`ADMIN_API_KEY` 仅用于管理台；`HIFLEET_KB_UPDATE_KEY` 仅用于显式授权的知识库维护。完整的用途、可选项和排障方式见主手册。

## 调用接口

### 同步 `/run`

```bash
curl -X POST http://127.0.0.1:10123/run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "船队轨迹为什么看不到？"}],
    "session_id": "websdk:tenant_a:user_100:c_001",
    "user_id": "user_100",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

### 流式 `/stream_run`

```bash
curl -N -X POST http://127.0.0.1:10123/stream_run \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "请介绍 HiFleet 轨迹功能"}],
    "session_id": "websdk:tenant_a:user_100:c_001",
    "user_id": "user_100",
    "source_channel": "websdk",
    "agent_profile": "customer_support"
  }'
```

该接口返回 `text/event-stream`。现有调用方也可继续使用 `/v1/chat/completions` 兼容入口；请求契约、多模态/微信格式和错误处理请查阅 [docs/CUSTOMER_SUPPORT.md](docs/CUSTOMER_SUPPORT.md)。
