# ROLLBACK — Shared Skills V2 回滚流程

## V2 loader 失败回退

当 V2 registry/manifest/lock 无法安全加载时，`customer_ceshi` 进入 V2 自有的
`safe_constrained` fallback（`src/skills_v2/fallback/safe_constrained.py`）：

- 不加载任何工具，不加载 legacy Skills。
- 注入保守 Prompt，禁止声称写入或数据查询成功。
- 运行时元数据 `mode = safe_constrained`。

这替换了旧的 `legacy_constrained` 行为：降级不再通过加载 legacy Skills 实现。

## hifleet-skills 上游版本回滚

```bash
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py rollback
```

`rollback` 会：

1. 从 `src/skills_v2/upstream/hifleet_skills/last_known_good/` 恢复 lock/manifest/SKILL.md
   到运行位置（恢复实际内容，不只是版本号）。
2. 将 lock 的 `commit` 指向 `lastKnownGood`。
3. 重新生成 `current/` 快照。
4. 重新运行一致性校验（`verify`），校验失败则以非零退出。

## customer_ceshi 模式回退（配置级）

`CUSTOMER_CESHI_SKILLS_MODE=legacy` 可让 `customer_ceshi` 进入配置级回退（仍是 V2 safe_constrained，
不加载 legacy Skills）。该开关仅用于配置级回退，不改变文件内容。

## customer_support

`customer_support` 始终保持 legacy 主链。V2 影子评估通过 `CUSTOMER_SUPPORT_SKILLS_SHADOW` 开关，
默认关闭，关闭时不影响任何客户回复。
