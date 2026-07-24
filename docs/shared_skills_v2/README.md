# Shared Skills V2 文档索引

Shared Skills V2 是共享业务契约层，不替换 legacy `SkillLoader`，也不合并两条 Agent Builder。它让 `customer_ceshi` 使用 manifest 驱动的 V2 工具与 Prompt，同时让 `customer_support` 保持 legacy 主链并执行可选、无工具的 V2 影子评估。

## 当前状态（2026-07-24）

| 项目 | 当前事实 | 入口 |
| --- | --- | --- |
| `customer_support` | 默认 legacy；`CUSTOMER_SUPPORT_SKILLS_SHADOW=true` 时可执行 V2 Prompt 影子评估，不改变客户回复、不执行影子工具或写入 | [MIGRATION_AND_ROLLBACK.md](MIGRATION_AND_ROLLBACK.md) |
| `customer_ceshi` | 默认 V2；加载失败时进入 `legacy_constrained`，不会恢复深度 Browser、写入或知识库管理能力 | [ARCHITECTURE.md](ARCHITECTURE.md)、[TESTING.md](TESTING.md) |
| 共享业务 Skill | 仅 `knowledge_retrieval`、`hifleet_data`、`ship_info_update` | [MANIFEST_SPEC.md](MANIFEST_SPEC.md) |
| 上游数据版本 | 使用 `skills-lock.json` 锁定；候选版本须通过静态契约检查后才可更新 | [UPSTREAM_SYNC.md](UPSTREAM_SYNC.md) |
| 完整切换条件 | 尚未满足：附件语义 5/5、扩展语料 ≥95%、P95 对比与生产灰度均未完成 | [../customer_ceshi_acceptance_status.md](../customer_ceshi_acceptance_status.md) |

## 最短阅读路径

1. **理解架构与边界**：[ARCHITECTURE.md](ARCHITECTURE.md) → [MANIFEST_SPEC.md](MANIFEST_SPEC.md)。
2. **修改工具或 Prompt**：先看 [LEGACY_V2_TOOL_MAPPING.md](LEGACY_V2_TOOL_MAPPING.md)，再修改 manifest/adapter，并补 `tests/skills_v2/`。
3. **修改写入流程**：[MIGRATION_AND_ROLLBACK.md](MIGRATION_AND_ROLLBACK.md) 的 Draft/确认边界；低层写入永不进入模型 schema。
4. **升级上游版本**：[UPSTREAM_SYNC.md](UPSTREAM_SYNC.md)；先 dry-run，再审查候选能力，不允许启动时 `git pull`。
5. **验证与排查**：[TESTING.md](TESTING.md) → [HTTP_VALIDATION.md](HTTP_VALIDATION.md) → [PERFORMANCE_AND_DIFF.md](PERFORMANCE_AND_DIFF.md)。

## 文档职责

| 文档 | 只回答什么问题 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 两条链路如何接入、哪些边界不可跨越 |
| [MANIFEST_SPEC.md](MANIFEST_SPEC.md) | manifest、Descriptor、版本元数据契约 |
| [LEGACY_V2_TOOL_MAPPING.md](LEGACY_V2_TOOL_MAPPING.md) | legacy 与 V2 工具的映射与禁止项 |
| [UPSTREAM_SYNC.md](UPSTREAM_SYNC.md) | 上游候选审计、lock、last-known-good |
| [MIGRATION_AND_ROLLBACK.md](MIGRATION_AND_ROLLBACK.md) | 模式切换、影子、回滚、Draft 规则 |
| [TESTING.md](TESTING.md) | 本地/HTTP/fixture 的测试证据与命令 |
| [HTTP_VALIDATION.md](HTTP_VALIDATION.md) | 已执行的外部接口验证事实 |
| [PERFORMANCE_AND_DIFF.md](PERFORMANCE_AND_DIFF.md) | 延迟与工具差异；未完成 P95 的限制 |
| [REGRESSION_CASES.yaml](REGRESSION_CASES.yaml) | 公共语义回归规格，不等同于已通过结果 |
| [BASELINE.md](BASELINE.md) | 重构开始时的可恢复基线 |

## 改动前检查清单

- 是否保持 `customer_support=legacy` 默认值与 `/run`、`/stream_run` 外部协议？
- 是否只使用共享 Descriptor，而不是新增平行工具白名单？
- 是否从模型工具、Prompt 和 fallback 中排除了低层写入、知识库管理和深度 Browser 搜索？
- 是否为平台 Claim、会员权限、格式或海图含义保留了可追溯证据？
- 是否补充了最小单测、对应链路回归和文档证据状态？

需要按反馈定位问题时，从项目级 [../DEVELOPER_TROUBLESHOOTING.md](../DEVELOPER_TROUBLESHOOTING.md) 进入；它会将现象映射到代码、配置、日志字段和测试。
