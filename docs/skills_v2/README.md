# Shared Skills V2

Shared Skills V2 已与 legacy Skills 完成**物理路径解耦**。两套 Skills 位于不同目录，
互不加载，可独立测试、维护和升级。

## 目录与边界

| 路径 | 角色 | 谁使用 |
| --- | --- | --- |
| `src/skills/` | legacy Skills（`SkillLoader` 驱动） | `customer_support` 默认主链 |
| `src/skills_v2/` | Shared Skills V2（manifest + adapter 驱动） | `customer_ceshi` |

- `src/skills_v2/` **禁止**导入 `skills.*`（legacy）。该边界由 `tests/skills_v2/test_boundaries.py`
  的 AST 扫描强制保证。
- `customer_ceshi`（`customer_ceshi_responses/` + `customer_ceshi_v2/`）只能导入 `skills_v2.*`。
- `customer_support` 不得导入 `skills_v2`；其回复行为保持 legacy 不变。V2 仅以**可选、无工具**
  的影子评估（shadow）方式介入，默认关闭。
- 两套路径互不加载：V2 loader 失败时进入 V2 自有的 `safe_constrained` fallback，**不会**回退加载 legacy Skills。

## 四项独立 V2 Skills

| Skill | 工具 | 说明 |
| --- | --- | --- |
| `knowledge_retrieval` | `local_kb_search` | 只读本地知识库检索，输出证据 ID/来源/匹配度/摘要 |
| `web_search` | `web_search` | 单次公开网页搜索；不含 `verify_public_page`/`agent_browser_deep_search`/`web_search_agent_browser` |
| `hifleet_data` | 14 个只读 HiFleet 数据工具 | 锁定、受审的只读数据适配器；不含任何写入或 browser 工具 |
| `ship_info_update` | `prepare_ship_update`/`commit_ship_update`/`cancel_ship_update` | 两阶段确认的船舶信息更新；底层写入工具归此 Skill，不进入 `hifleet_data` |

## customer_support 默认保持 legacy

- `resolve_skill_runtime("customer_support")` 默认返回 `legacy`。
- 影子评估通过 `CUSTOMER_SUPPORT_SKILLS_SHADOW=true` 显式开启，仅注入 V2 Prompt 做**无工具**比对，
  不改变客户回复、不执行写入。
- 回归保护见 `tests/skills_v2/test_shared_registry.py` 与 `tests/skills_v2/test_customer_support_shadow_graph.py`。

## hifleet-skills 上游检查/准备/启用/回滚

上游仓库：`https://github.com/charleiWang/hifleet-skills`。运行时**绝不**直接 clone/pull 上游 HEAD，
只加载本地受审快照。版本、commit、内容哈希、能力清单来自独立 V2 lock：
`src/skills_v2/upstream/hifleet_skills/lock.json`（与仓库根 `skills-lock.json` 分离）。

同步脚本：`scripts/skills_v2/sync_hifleet_skills.py`

```bash
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py status
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py check
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py prepare --revision <commit>
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py apply --revision <commit>
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py verify
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py rollback
```

- `check` 只读比对上游 HEAD，输出 `NO_UPDATE` 或 `UPDATE_AVAILABLE`，不修改任何运行文件。
- `prepare` 把候选克隆到临时目录完成可信仓库/结构/语法/API Host/环境变量/能力识别/风险分类校验，
  候选报告写入 `docs/skills_v2/upstream-update-report.md`，候选**不会**直接成为 current。
- `apply` 只对已受审候选执行 lock/manifest/SKILL.md/current 的原子更新，保留上一版本为 last-known-good；
  新增上游能力**不会**自动进入批准清单。
- `verify` 校验 lock/manifest/SKILL.md/current 一致、批准能力有本地映射+输入 Schema+测试、待审核能力未暴露、
  hifleet_data 无 browser/写入越权。
- `rollback` 切回 last-known-good 并重新校验。

详细说明见 [UPSTREAM_SYNC.md](UPSTREAM_SYNC.md)、[ROLLBACK.md](ROLLBACK.md)。

## 文档索引

- [ARCHITECTURE.md](ARCHITECTURE.md) - 架构与不可跨越边界
- [UPSTREAM_SYNC.md](UPSTREAM_SYNC.md) - 上游同步机制
- [MAINTENANCE_PROMPT.md](MAINTENANCE_PROMPT.md) - 固定维护提示词（供 Agent 自动检查上游更新）
- [TESTING.md](TESTING.md) - 测试命令与证据
- [ROLLBACK.md](ROLLBACK.md) - 回滚流程
- [upstream-update-report.md](upstream-update-report.md) - 最近一次候选报告
