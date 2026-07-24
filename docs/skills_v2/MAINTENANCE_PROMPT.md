# MAINTENANCE_PROMPT — hifleet-skills 上游维护提示词

> 这是一个**固定提示词**，供后续 Agent 自动检查 hifleet-skills 上游更新时复用。
> 不要改写本提示词的步骤与安全约束；只允许在“执行结果”区追加事实。

## 固定维护提示词（复制即用）

```
你是 HiFleet Agent 仓库的维护助手。请按以下步骤检查 hifleet-skills 上游更新，全程不得
绕过受审流程，不得在运行时直接 clone/pull 上游 HEAD，不得把未审核上游代码作为运行版本。

1. 运行 `PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py status`，
   记录当前本地运行版本、upstream commit、content hash、last-known-good、已批准与待审核能力。
2. 运行 `check`，只读比对上游 HEAD。若输出 NO_UPDATE，结束并报告“无更新”。
   若输出 UPDATE_AVAILABLE，记录本地与上游 commit。
3. 对上游新 commit 运行 `prepare --revision <upstream_commit>`：
   - 候选会被克隆到临时目录完成可信仓库、文件结构、Python 语法、API Host、环境变量、
     新增/删除/变更能力识别与读写风险分类校验。
   - 候选报告写入 docs/skills_v2/upstream-update-report.md。
   - 候选不得直接成为 current。
4. 审查候选报告：新增上游能力默认 review_required，只有在 capability_map.yaml 中明确配置
   并具备契约测试后才能转为 approved。不得自动暴露账户、充值、支付、开票、控制台、
   联系人解锁、写入或其他高风险能力。
5. 若决定启用：运行 `apply --revision <upstream_commit>`。lock、manifest、SKILL.md、current
   指针必须原子更新；任一步骤失败则全部回退到旧版本，last-known-good 保留上一版本。
6. 运行 `verify` 确认 lock/manifest/SKILL.md/current 一致、批准能力有本地映射+输入 Schema+测试、
   待审核能力未暴露、hifleet_data 无 browser/写入越权。
7. 若 verify 失败或线上异常：运行 `rollback` 切回 last-known-good 并重新校验。
   禁止只改版本号而不恢复实际内容。
8. 最后运行：
   PYTHONPATH=src .venv/bin/python -m pytest tests/skills_v2 -q
   PYTHONPATH=src .venv/bin/python -m pytest tests/customer_ceshi tests/customer_ceshi_v2 -q
   并确认 customer_support 仍为 legacy（只读保护回归通过）。

执行结果区（追加事实，不要改写上述步骤）：
- status/check/prepare/apply/verify/rollback 的实际输出摘要
- 起始与最终 HEAD
- 批准与待审核能力清单
- 测试数量与 skipped/xfail/failed
```

## 安全约束（不可违反）

- 运行时只加载本地受审快照，V2 lock 为 `src/skills_v2/upstream/hifleet_skills/lock.json`。
- 新增上游能力默认不可见；未写入 `capability_map.yaml` 且无契约测试的能力保持 `review_required`。
- 上游同步失败不改变 current 与 last-known-good。
- 不得让 `customer_support` 改为导入或加载 V2。
