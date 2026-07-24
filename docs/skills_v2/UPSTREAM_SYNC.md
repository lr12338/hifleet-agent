# hifleet-skills 同步与版本锁定

当前上游 lock 为 `hifleet-skills` 版本 `0.3.21`，commit `e4acf599192f3f1d247ef2da00e78d0cff89819c`，
记录于 `src/skills_v2/upstream/hifleet_skills/lock.json`（与仓库根 `skills-lock.json` 分离）。

## 闭环同步流程（候选 -> 审计 -> 快照 -> 运行时 -> 回滚）

`scripts/skills_v2/sync_hifleet_skills.py` 实现完整链路，生产请求中绝不 clone 或执行上游代码：

1. **候选审计**（`prepare`）- 在临时目录克隆指定 revision，记录 commit，校验可信仓库、必要文件、
   `SKILL.md` 版本/环境变量、HiFleet API Host、上游脚本 Python 语法。新脚本默认 `review_required`，
   永不自动暴露。候选报告写入 `docs/skills_v2/upstream-update-report.md`。
2. **受控快照**（`apply`）- 只对 `validated` 候选执行，用同一记录原子更新三个产物：
   - `lock.json`（版本/commit/lastKnownGood/contentHash/能力清单/环境变量）
   - `manifest.yaml`（skill_version/upstream_commit 同步；项目控制的 adapter 能力列表不自动扩展）
   - `SKILL.md`（用上游来源、批准/待审核能力列表与映射重新生成）
3. **运行时** - `SharedSkillRegistry` 通过 `upstream_lock_key: hifleet-skills` 读取 lock，覆盖
   `skill_version`/`upstream_commit`；适配器在 `source_versions` 携带 `content_hash`/`last_known_good`。
4. **回滚**（`rollback`）- 校验失败时恢复 last-known-good 实际内容（非仅版本号），lock 指向 lastKnownGood，
   重新生成 current 快照并重新校验。

## 命令

```bash
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py status
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py check
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py prepare --revision <commit>
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py apply --revision <commit>
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py verify
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py rollback
```

## 安全保证

- 候选不直接成为 current
- apply 任一步骤失败全部回退到旧版本
- apply 失败不改 current 与 last-known-good
- 新增上游能力默认 `review_required`，须在 `capability_map.yaml` 配置 + 契约测试才能转 `approved`
- 不得自动暴露账户、充值、支付、开票、控制台、联系人解锁、写入等高风险能力

## 能力映射治理

`src/skills_v2/upstream/hifleet_skills/capability_map.yaml` 是能力暴露的治理清单。详见
[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) 第五节。
