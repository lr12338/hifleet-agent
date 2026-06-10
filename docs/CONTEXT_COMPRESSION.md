# Context Compression（当前版本）

## 1. 目标

降低主 Agent 的上下文噪声，只保留高频、刚需能力，提升路由准确率与工具调用稳定性。

## 2. 裁剪结果

保留：

- `knowledge_qa`（`smart_search`）
- `hifleet_ship_service`（8 个船舶工具）

移除：

- `lead_collection`
- `session_summary`
- `human_handoff`

## 3. 压缩策略

1. **能力域压缩**：从多能力域收敛到知识检索 + 船舶服务
2. **工具面压缩**：删除三项能力对应工具，仅保留 9 个核心工具
3. **Prompt 压缩**：移除线索收集、会话总结、转人工策略相关规则段
4. **遗留链路压缩**：清理 workflow 中 `lead_collection` 意图与节点

## 4. 对运行时的影响

- `src/agents/agent.py` 仅白名单加载两个技能的 `SKILL.md`
- `src/skills/skill_loader.py` 仅映射两个技能并输出对应工具
- `config/agent_llm_config.json` 工具清单已同步裁剪

## 5. 回归关注点

1. `smart_search` 能正常命中 RAG 数据集
2. 船舶查询和更新调用链正常
3. 不再出现 `collect_business_lead` / `upload_session_summary` / `human_handoff` 相关触发
