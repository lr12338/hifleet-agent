# 可恢复基线

- 仓库：`git@github.com:lr12338/hifleet-agent.git`
- 分支：`codex/shared-skills-v2`
- 起始 HEAD：`f406ae78440a7226c89ffd7dd547040b575c6eed`

## V2 物理解耦基线

V2 已从 `src/skills/` 迁移至独立 `src/skills_v2/`，两套系统互不加载。legacy `src/skills/` 保留
knowledge_qa、hifleet_ship_service、browser_verify 等技能，继续服务 `customer_support` legacy 链路。

## hifleet-skills 上游基线

当前锁定版本 `0.3.21`（commit `e4acf599192f3f1d247ef2da00e78d0cff89819c`），记录于
`src/skills_v2/upstream/hifleet_skills/lock.json`（内容哈希 `7118592b…0f8a`）。

上游声明 `HIFLEET_API_KEY`，默认 `https://api.hifleet.com`，同时包含账户、充值、注册、控制台、
联系人解锁等流程与只读数据 API。账户与写入类能力不暴露。

V2 lock 为**单源真相**。受审候选发现 16 个上游脚本：13 个只读数据脚本可显式映射到适配器工具，
`charter_contact_dedup`、`charter_enrich_helpers`、`open_console` 保持待审核，不自动暴露。
