# HiFleet Agent 文档中心

本文档中心用于快速定位当前实现、接口、验证证据和问题排查入口。**不迁移、不删除既有详细文档**；本文件只定义阅读顺序和权威入口，避免同一信息在多个文件中漂移。

## 先看什么

| 你的目标 | 首选文档 | 继续阅读 |
| --- | --- | --- |
| 本地启动、调用接口或接入客户端 | [../README.md](../README.md) | [CUSTOMER_SERVICE_API.md](CUSTOMER_SERVICE_API.md) |
| 排查用户反馈、工具调用或回复异常 | [DEVELOPER_TROUBLESHOOTING.md](DEVELOPER_TROUBLESHOOTING.md) | 对应 Profile 手册与测试 |
| 维护正式客服链路 | [CUSTOMER_SUPPORT.md](CUSTOMER_SUPPORT.md) | [AGENT_TECHNICAL_DOCUMENTATION.md](AGENT_TECHNICAL_DOCUMENTATION.md) |
| 维护 `customer_ceshi` | [CUSTOMER_CESHI_ARCHITECTURE.md](CUSTOMER_CESHI_ARCHITECTURE.md) | [customer_ceshi_acceptance_status.md](customer_ceshi_acceptance_status.md) |
| 维护 Shared Skills V2 | [shared_skills_v2/README.md](shared_skills_v2/README.md) | `shared_skills_v2/` 下专题文档 |
| 维护知识库内容 | [KNOWLEDGE_BASE_GUIDE.md](KNOWLEDGE_BASE_GUIDE.md) | [CUSTOMER_SUPPORT_KB_OPERATIONS.md](CUSTOMER_SUPPORT_KB_OPERATIONS.md) |

## 当前运行状态（2026-07-24）

| 链路 | 默认模式 | 面向对象 | 关键边界 | 首选证据 |
| --- | --- | --- | --- | --- |
| `customer_support` | `legacy` | 正式客户请求 | 主链保持 legacy；可选 V2 无工具影子评估不改变客户回复 | [CUSTOMER_SUPPORT.md](CUSTOMER_SUPPORT.md)、[shared_skills_v2/MIGRATION_AND_ROLLBACK.md](shared_skills_v2/MIGRATION_AND_ROLLBACK.md) |
| `customer_ceshi` | `v2` | 验证与受控实验 | Shared Skills V2、Responses/Chat 回退、Draft 确认、Claim Guard | [CUSTOMER_CESHI_ARCHITECTURE.md](CUSTOMER_CESHI_ARCHITECTURE.md)、[customer_ceshi_acceptance_status.md](customer_ceshi_acceptance_status.md) |
| Shared Skills V2 | manifest 驱动 | 两条链路的共享业务契约 | 仅 `knowledge_retrieval`、`hifleet_data`、`ship_info_update`；低层写入不暴露给模型 | [shared_skills_v2/README.md](shared_skills_v2/README.md) |

`customer_ceshi` 的 V2 加载失败时会进入 `legacy_constrained`：仍可使用受限只读能力，但会拒绝写入、知识库管理和自主 Browser 搜索；原因只以异常类别写入 trace，不记录敏感信息。

## 文档分层

```text
README.md                         启动与最小调用路径
docs/README.md                    本导航：当前状态与阅读顺序
├── CUSTOMER_SERVICE_API.md       对外 HTTP/SSE 契约
├── CUSTOMER_SUPPORT.md           正式客服运行与运维规则
├── CUSTOMER_CESHI_ARCHITECTURE.md 实验链路架构与开发边界
├── DEVELOPER_TROUBLESHOOTING.md  反馈问题的统一排查路径
├── customer_ceshi_acceptance_status.md 验收事实账本
├── shared_skills_v2/             V2 架构、迁移、验证与版本锁定
├── RAG/                          知识库内容与索引
└── archive/                      历史材料，不作为当前实现依据
```

## 权威性与更新规则

1. **运行行为**以代码、配置和自动化测试为准；文档必须链接到相应入口。
2. **接口字段**只在 [CUSTOMER_SERVICE_API.md](CUSTOMER_SERVICE_API.md) 定义；其他文档只链接，不复制完整契约。
3. **验收结论**只在 [customer_ceshi_acceptance_status.md](customer_ceshi_acceptance_status.md) 记录；`PASSED`、`MOCK`、`NOT_COMPLETE`、`BLOCKED` 必须严格区分。
4. **Shared Skills V2** 的迁移、版本锁定和回滚信息只在 [shared_skills_v2/README.md](shared_skills_v2/README.md) 的索引范围内维护。
5. 新增文档前先判断应当补充到“接口、运行、排查、验收、知识库、历史”中的哪一类；避免再创建平行总览。

## 反馈问题最小信息

提交问题时请先按 [DEVELOPER_TROUBLESHOOTING.md](DEVELOPER_TROUBLESHOOTING.md) 收集：发生时间、环境、`agent_profile`、`source_channel`、脱敏 `session_id`/`run_id`、请求摘要、客户可见回复、`metrics`/`route_trace` 摘要、期望结果和复现步骤。禁止粘贴密钥、Cookie、Token、签名 URL 或完整客户隐私数据。
