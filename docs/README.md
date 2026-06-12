# HiFleet Agent 文档入口

本文是当前仓库文档索引。新同学或排障时先看这里，避免在历史文档中来回跳转。

## 推荐阅读顺序

| 目标 | 文档 |
| --- | --- |
| 理解当前 Agent 架构、消息链路、工具路由 | [AGENT_TECHNICAL_DOCUMENTATION.md](AGENT_TECHNICAL_DOCUMENTATION.md) |
| 接入 `/run`、`/stream_run`，处理多用户会话 | [API_MULTI_USER_INTEGRATION.md](API_MULTI_USER_INTEGRATION.md) |
| 管理台使用、日志查询、调试入口 | [ADMIN_BACKEND_SYSTEM_GUIDE.md](ADMIN_BACKEND_SYSTEM_GUIDE.md) |
| 客服 Agent 真实 API 回归、测试船舶、写操作验证 | [CUSTOMER_SUPPORT_AGENT_REGRESSION.md](CUSTOMER_SUPPORT_AGENT_REGRESSION.md) |
| 知识库与 `smart_search` 分层检索 | [KNOWLEDGE_BASE_GUIDE.md](KNOWLEDGE_BASE_GUIDE.md) |
| 内部员工表格/Python 沙盒闭环 | [EMPLOYEE_ASSISTANT_SANDBOX_RUNBOOK.md](EMPLOYEE_ASSISTANT_SANDBOX_RUNBOOK.md) |

## 当前主链路

```mermaid
flowchart LR
    Client[渠道/调用方] --> API[src/main.py]
    API --> Profile[profile 解析]
    Profile --> Graph[统一 phase graph]
    Graph --> CS[customer_support<br/>route -> plan -> act -> check -> loop/finalize]
    Graph --> Employee[employee_assistant<br/>route -> plan -> act -> check -> loop/finalize]
    API --> Obs[observability]
```

## 文档维护规则

- 架构变化先更新 `AGENT_TECHNICAL_DOCUMENTATION.md`。
- 新增客服回归场景先更新 `scripts/hifleet_agent_regression.py`，再更新 `CUSTOMER_SUPPORT_AGENT_REGRESSION.md`。
- 一次性导入报告、过期方案和历史记录放入 `docs/archive/`，不要作为主入口。
- 文档中不要写入 API key、token、数据库密码或真实用户隐私数据。
