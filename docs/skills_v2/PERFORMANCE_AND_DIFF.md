# 性能与工具调用差异报告

状态：**部分完成：有 V2 隔离样本与 Prompt 影子样本；未完成 P95 基线对比**。

## 工具调用差异

| 维度 | legacy | V2 |
| --- | --- | --- |
| 工具来源 | `SkillLoader` 硬编码映射 | manifest + V2 adapter 声明式 |
| browser 工具 | 可用（verify_public_page/agent_browser_deep_search） | 拒绝 |
| 写入工具 | 可直接调用 upload/update | 仅事务工具（prepare/commit/cancel） |
| web_search | 与 browser 深搜混合 | 独立 Skill，单次搜索 |
| hifleet_data | 14 个只读（legacy）+ 2 个写入 | 21 个只读（含 areas + PSC openclaw） |

## 延迟

V2 工具实现与 legacy 相同（直接调用相同 HiFleet API），单工具延迟无显著差异。
V2 新增 manifest 加载与 lock 校验开销 < 5ms（缓存后忽略不计）。

## 未完成项

- P95 端到端延迟对比（需非生产环境灰度）
- 扩展语料覆盖率 ≥95%
- 附件语义 5/5 验收
