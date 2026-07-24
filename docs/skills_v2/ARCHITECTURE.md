# Shared Skills V2 架构

> 中文阅读入口与专题职责见 [README.md](README.md)。本文件说明实现边界与不可跨越约束。

## 运行时边界

`src/skills_v2/core/` 是独立的 manifest 驱动层，不修改也不替换 `src/skills/skill_loader.py`；
legacy 调用方保持原有 Prompt 与工具加载行为。`config/agent_profiles.json` 控制运行时选择。

| 链路 | 默认模式 | 适配器 | 用户可见行为 |
| --- | --- | --- | --- |
| `customer_support` | `legacy` | `skills_v2.adapters.customer_support_shadow` | legacy 客户回复保持主链；可选 V2 影子注入 Skill Prompt 做无工具评估。 |
| `customer_ceshi` | `v2` | `skills_v2.adapters.customer_ceshi` | Responses/Chat 运行时接收 V2 描述符、工具与 Skill 指令；V2 加载失败进入 safe_constrained 降级（不加载 legacy Skills）。 |

`CUSTOMER_SUPPORT_SKILLS_MODE` 和 `CUSTOMER_CESHI_SKILLS_MODE` 可不改代码覆盖模式，非法值回退到安全默认。

## 共享契约

`ToolDescriptor` 承载工具名、JSON Schema、风险等级、超时、确认标记、Skill 版本与上游 commit。
customer_ceshi 适配器将其转为 Responses API JSON；customer_support 适配器暴露相同描述符用于影子评估。
协议封装不同，业务契约一致。

V2 包含四项业务 Skill：

- `knowledge_retrieval`：内部 `local_kb_search`，仅返回带来源的证据。
- `web_search`：单次公开网页搜索 `web_search`，只保留 URL/标题/摘要/来源类型。
- `hifleet_data`：经审只读 HiFleet 数据能力（21 个工具）。
- `ship_info_update`：事务级 Draft 工具；底层写入仅内部使用。

customer_ceshi V2 仅暴露 `web_search` 做公开网页证据；`verify_public_page`、
`agent_browser_deep_search`、`web_search_agent_browser` 被拒绝且不向模型描述。弱匹配、冲突或
非官方网页结果须保守回答或追问；无 browser 工具可二次搜索。

## 上游元数据单源真相

`src/skills_v2/upstream/hifleet_skills/lock.json` 是 `hifleet_data` 上游版本、commit、内容哈希、
已批准只读能力与所需环境变量的权威记录。manifest 声明 `upstream_lock_key: hifleet-skills`；
运行时 `SharedSkillRegistry` 用 lock 覆盖 `skill_version`/`upstream_commit`，适配器在
`source_versions` 中携带 `content_hash`/`last_known_good`。`sync_hifleet_skills.py apply` 用同一受审
候选更新 lock、manifest 快照与 `SKILL.md`，确保 lock、manifest、Prompt 与运行时元数据不偏离。
新上游能力报告为 `review_required`，永不自动加入 manifest 或暴露给 agent。

## 可观测性

归一化的 V2 结果携带 `skill_id`、`skill_version`、`upstream_commit` 和 `capability`。它们是 trace
元数据，不证明自然语言主张在语义上正确。密钥、Cookie、Token 与签名 URL 不记录。
