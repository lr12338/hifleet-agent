# 双链路迁移、影子与回滚

## 当前状态

| 链路 | 默认模式 | 说明 |
| --- | --- | --- |
| `customer_support` | legacy | 主链保持 legacy 回复；V2 仅做可选无工具影子评估 |
| `customer_ceshi` | v2 | 加载 V2 工具与 Prompt；失败进入 safe_constrained 降级 |

## 模式切换

```bash
# customer_ceshi 切换为配置级回退（仍走 V2 safe_constrained，不加载 legacy）
CUSTOMER_CESHI_SKILLS_MODE=legacy

# customer_support 开启 V2 影子评估（不改回复，仅注入 Prompt 做无工具比对）
CUSTOMER_SUPPORT_SKILLS_SHADOW=true
```

## V2 加载失败降级

当 V2 registry/manifest/lock 无法安全加载时，记录异常类并使用 `safe_constrained` 运行时。
该 fallback 无工具、注入保守 Prompt、不加载 legacy Skills。运行时元数据 `mode = safe_constrained`。

## 影子评估

`customer_support` 的 V2 影子评估通过 `compare_legacy_trace_with_v2()` 构建：
- 注入 V2 Skill Prompt 到无工具模型
- 不执行任何工具、不重放写入
- 仅产出契约级比对记录（工具选择差异、证据数量、写入状态）

## 回滚

详见 [ROLLBACK.md](ROLLBACK.md)。两种场景：
- V2 loader 失败：自动进入 safe_constrained
- 上游版本回退：`sync_hifleet_skills.py rollback`
