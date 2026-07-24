# config/skills_v2/

V2 运行时配置说明。实际 `skill_runtime` 模式由 `src/skills_v2/core/policy.py` 从
`config/agent_profiles.json` 及以下环境变量解析；`skill_runtime.json` 记录 V2 默认值与 V2 专用设置。

| 链路 | 默认 | 环境变量 | 降级 |
| --- | --- | --- | --- |
| `customer_ceshi` | `v2` | `CUSTOMER_CESHI_SKILLS_MODE` | `safe_constrained`（不加载 legacy Skills） |
| `customer_support` | `legacy` | `CUSTOMER_SUPPORT_SKILLS_MODE` | 保持 legacy；影子评估用 `CUSTOMER_SUPPORT_SKILLS_SHADOW`（默认关闭） |
