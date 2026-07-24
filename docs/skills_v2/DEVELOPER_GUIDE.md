# Shared Skills V2 开发指南

本指南面向维护 `customer_ceshi` 链路的开发者，系统介绍 Shared Skills V2 的开发逻辑、
调用方式与更新维护操作。V2 已与 legacy Skills 完成**物理路径解耦**，两套系统位于不同目录、
互不加载、可独立测试与升级。

---

## 一、设计目标与核心约束

### 设计目标

| 目标 | 说明 |
| --- | --- |
| 物理解耦 | V2 迁移到独立 `src/skills_v2/`，不再与 legacy `src/skills/` 共享代码路径 |
| 独立加载 | V2 自有 registry/loader/manifest/adapter，运行时不依赖 legacy `SkillLoader` |
| 安全降级 | V2 loader 失败进入 V2 自有 `safe_constrained` fallback，**不回退加载 legacy** |
| 受审上游 | hifleet-skills 上游运行时只加载本地受审快照，绝不直接 clone/pull 上游 HEAD |
| 能力治理 | 新增上游能力默认不可见，只有写入 `capability_map.yaml` 且具备契约测试才能暴露 |

### 核心约束（不可违反）

- `src/skills_v2/` **禁止**导入 `skills.*`（legacy）。由 AST 边界测试强制保证。
- `customer_ceshi` 只能导入 `skills_v2.*`。
- `customer_support` 不得导入 `skills_v2`，默认保持 legacy 回复行为不变。
- V2 lock（`src/skills_v2/upstream/hifleet_skills/lock.json`）与仓库根 `skills-lock.json` 分离。
- `hifleet_data` 只含只读工具，**不含**写入或 browser 工具；底层写入归 `ship_info_update`。

---

## 二、目录结构与职责

```
src/skills_v2/
├── __init__.py
├── core/                       # V2 核心层
│   ├── descriptors.py          # ToolDescriptor / SkillManifest / SkillRuntimeBundle 契约
│   ├── policy.py               # 运行时模式解析 + profile 工具白名单策略
│   ├── manifest_loader.py      # 加载并校验 manifest.yaml
│   ├── registry.py             # 从 manifest + adapter 组装 descriptors/tools/prompt
│   ├── loader.py               # 从 V2 各 skill adapter 收集工具实现（替代 legacy SkillLoader）
│   ├── lock_store.py           # 读取 V2 lock（单源真相）
│   ├── result_normalizer.py    # 归一化工具结果元数据
│   └── errors.py               # V2 异常类型
├── adapters/                   # profile 适配层
│   ├── customer_ceshi.py       # 组装 customer_ceshi 运行时 bundle
│   └── customer_support_shadow.py  # customer_support 可选影子评估（默认关闭）
├── skills/                     # 四项独立 V2 Skill
│   ├── knowledge_retrieval/    # 本地知识库检索
│   ├── web_search/             # 单次公开网页搜索
│   ├── hifleet_data/           # 锁定只读 HiFleet 数据
│   └── ship_info_update/       # 两阶段确认的船舶信息更新
├── upstream/hifleet_skills/    # 上游受审快照与治理
│   ├── lock.json               # V2 上游 lock（版本/commit/哈希/能力清单）
│   ├── capability_map.yaml     # 能力映射（approved / review_required）
│   └── schemas/                # 每个能力的 input/output JSON Schema
└── fallback/
    └── safe_constrained.py     # V2 loader 失败的安全降级 bundle
```

配套路径：

| 路径 | 职责 |
| --- | --- |
| `config/skills_v2/` | V2 运行时配置说明与默认值文档 |
| `scripts/skills_v2/` | 上游同步脚本 `sync_hifleet_skills.py` + 回归脚本 |
| `tests/skills_v2/` | V2 核心测试、边界测试、能力契约测试 |
| `docs/skills_v2/` | V2 全部文档 |

---

## 三、开发逻辑（架构与调用链）

### 3.1 运行时模式解析

模式由 `src/skills_v2/core/policy.py:resolve_skill_runtime()` 解析，优先级如下（高到低）：

```
环境变量 {PROFILE}_SKILLS_MODE  >  config/agent_profiles.json 的 skill_runtime  >  内置默认
```

内置默认：

| Profile | 默认模式 | 环境变量覆盖 | 允许值 |
| --- | --- | --- | --- |
| `customer_ceshi` | `v2` | `CUSTOMER_CESHI_SKILLS_MODE` | `v2` / `legacy` |
| `customer_support` | `legacy` | `CUSTOMER_SUPPORT_SKILLS_MODE` | `legacy` |

`customer_support` 的影子评估单独由 `CUSTOMER_SUPPORT_SKILLS_SHADOW` 控制，默认 `false`。

### 3.2 核心调用链：manifest → registry → bundle

V2 的核心数据流是**声明式**的：manifest 声明能力，registry 依据 manifest 从 adapter 取真实工具实现，
组装成 `SkillRuntimeBundle` 交给 agent 链路。

```
manifest.yaml ──load_manifest()──> SkillManifest
                                          │
V2 skill adapter (@tool) ──loader──────┐  │
                                       ▼  ▼
                              SharedSkillRegistry
                                  ├─ descriptors_for()  → ToolDescriptor[]（含 schema/权限/版本）
                                  ├─ tools_for()        → 可执行工具实例[]
                                  └─ prompt_for()       → 拼接各 SKILL.md Prompt
                                          │
                                          ▼
                              SkillRuntimeBundle
                              (mode / tools / descriptors / prompt / source_versions)
                                          │
                              ┌───────────┴────────────┐
                              ▼                        ▼
                  customer_ceshi adapter      customer_support_shadow adapter
                  (build_customer_ceshi_       (build_customer_support_shadow_
                   bundle)                      bundle / compare_legacy_trace_)
```

**关键点：工具实现的唯一来源是 V2 adapter。** `loader.py` 直接 import 四个 V2 skill adapter
（`knowledge_retrieval`、`web_search`、`hifleet_data`、`ship_info_update`），
通过 `__all__` 或 `get_*_tools()` 收集 `@tool` 对象，**完全不经过 legacy `SkillLoader`**。

### 3.3 registry 三步组装

`SharedSkillRegistry`（`src/skills_v2/core/registry.py`）提供三个核心方法：

1. **`load_manifests(skill_ids)`** — 加载各 skill 的 `manifest.yaml`，并用 V2 lock 覆盖上游版本/commit
   （`_apply_lock_authority`：lock 是版本真相，manifest 里的硬编码值不作数）。
2. **`descriptors_for(skill_ids)`** — 遍历 manifest 的 capabilities，对每个工具：
   - 校验是否在 profile 白名单内（`profile_allows_tool`）
   - 非事务工具从 adapter 取真实 `args_schema` 生成 JSON Schema
   - 事务工具（`prepare/commit/cancel_ship_update`）用 manifest 内联的 `input_schema`
   - 产出 `ToolDescriptor`（含 `read_only`、`risk_level`、`upstream_commit` 等元数据）
3. **`tools_for(descriptors)`** — 按 descriptor 名称从 loader 取可执行工具实例
   （事务工具不取实例，由 agent 层的 `ShipUpdateGate` 处理）。

### 3.4 四项 Skill 的设计

| Skill | 暴露工具 | adapter 入口 | 设计要点 |
| --- | --- | --- | --- |
| `knowledge_retrieval` | `local_kb_search` | `adapter.py`（3 行，调用 `local_kb_runtime.search_local_kb_structured`） | 只读；输出证据 ID/来源/匹配度/摘要；弱匹配不作确定结论 |
| `web_search` | `web_search` | `adapter.py`（含 `_volc_web_search`/`_ark_web_search` 引擎 + 降级） | 只暴露 `web_search`；排除 `verify_public_page`/`agent_browser_deep_search`/`web_search_agent_browser`；保留 URL/标题/摘要/来源类型 |
| `hifleet_data` | 21 个只读工具 | `adapter.py` + `scripts/`（auth/各 API 脚本） | 锁定只读；无写入/browser 工具；版本来自 V2 lock |
| `ship_info_update` | `prepare/commit/cancel_ship_update`（事务）+ `upload_ship_position`/`update_ship_static_info`（底层写入，归此 Skill） | `adapter.py` + `scripts/` + `schemas.py` + `validators.py` | 两阶段确认；底层写入不进入模型 schema；确定性校验 |

**工具隔离原则**：`hifleet_data` 的 `adapter.py` 只含只读工具与只读 `_ensure_imports()`；
`ship_info_update` 的 `adapter.py` 含写入工具与写入版 `_ensure_imports()`。两套 scripts 目录独立，
避免写入能力泄露到只读 Skill。

### 3.5 Fallback 机制

当 V2 registry/manifest/lock 无法安全加载时，`customer_ceshi` 链路进入
`build_safe_constrained_bundle()`（`src/skills_v2/fallback/safe_constrained.py`）：

- `tools = ()`，`descriptors = ()`（不加载任何工具）
- 注入保守 Prompt（禁止声称写入/查询成功）
- `mode = "safe_constrained"`

agent builder（`customer_ceshi_responses/builder.py`）的降级逻辑：

```
请求 v2 → try build_customer_ceshi_bundle()
              ├─ 成功 → mode="v2"，注入 V2 工具+Prompt
              └─ 失败 → build_safe_constrained_bundle()，mode="safe_constrained"
                                                      （不加载 legacy skills）
```

这替换了旧的 `legacy_constrained` 行为——降级不再通过加载 legacy Skills 实现。

### 3.6 边界约束机制

边界由 `tests/skills_v2/test_boundaries.py` 的 10 项测试强制：

1. AST 扫描 `src/skills_v2/` 禁止导入 `skills.*`
2. `customer_ceshi` 只能导入 `skills_v2`
3. `customer_support` 不得导入 `skills_v2`
4. `tests/skills_v2` 不依赖 legacy fixture
5. safe fallback 不加载 legacy skills
6. 独立子进程验证 V2 核心不依赖 legacy `skill_loader` 的 import cache
7. V2 lock 与 legacy 根 lock 分离
8. 上游 apply 失败不改 current/last-known-good
9. 新增上游能力默认不可见
10. web_search Skill 只存在 `web_search`

---

## 四、调用方式

### 4.1 在 agent 链路中接入

`customer_ceshi` 的两个 builder 已内置 V2 接入，无需手动调用 registry：

- `src/agents/customer_ceshi_responses/builder.py` — Responses API 链路，构建时自动调用
  `build_customer_ceshi_bundle()` 获取 V2 工具与 Prompt，失败时降级到 safe_constrained。
- `src/agents/customer_ceshi_v2/tools.py` — `CapabilityRegistry` 默认从
  `skills_v2.core.loader.available_tool_names()` 取全量 V2 工具。

`customer_support` 的影子评估（默认关闭）在 `src/agents/agent.py` 中：

```python
# agent.py 仅在 CUSTOMER_SUPPORT_SKILLS_SHADOW=true 时触发
from skills_v2.core.policy import customer_support_shadow_enabled
from skills_v2.adapters.customer_support_shadow import compare_legacy_trace_with_v2
```

影子评估**不执行工具、不改回复**，仅注入 V2 Prompt 做无工具比对记录。

### 4.2 环境变量与配置

| 变量 / 配置 | 作用 | 默认 |
| --- | --- | --- |
| `CUSTOMER_CESHI_SKILLS_MODE` | customer_ceshi 运行模式 | `v2` |
| `CUSTOMER_SUPPORT_SKILLS_MODE` | customer_support 运行模式 | `legacy` |
| `CUSTOMER_SUPPORT_SKILLS_SHADOW` | 是否开启 customer_support V2 影子评估 | `false` |
| `COZE_WORKSPACE_PATH` | 工作区根路径（影响 lock/manifest 定位） | 仓库根 |
| `config/agent_profiles.json` 的 `skill_runtime` | 持久化模式配置 | 见 `config/skills_v2/skill_runtime.json` |

### 4.3 程序化调用示例

```python
from skills_v2.adapters.customer_ceshi import build_customer_ceshi_bundle

# 组装 customer_ceshi V2 运行时 bundle
bundle = build_customer_ceshi_bundle()
print(bundle.mode)                         # "v2"
print([d.name for d in bundle.descriptors])  # 26 个工具描述符
print([t.name for t in bundle.tools])         # 23 个可执行工具
print(bundle.source_versions["hifleet_data"]) # lock 锚定的版本/commit/哈希

# 直接取单个工具
from skills_v2.core.loader import get_tool
tool = get_tool("get_ship_position")  # 返回 @tool 实例

# 列出全部可用 V2 工具名
from skills_v2.core.loader import available_tool_names
print(available_tool_names())

# 查询当前上游 lock 状态
from skills_v2.core.lock_store import hifleet_lock_record
record = hifleet_lock_record()
print(record["version"], record["commit"], record["approvedReadOnlyCapabilities"])
```

---

## 五、更新维护操作

### 5.1 hifleet-skills 上游同步（6 个命令）

同步脚本：`scripts/skills_v2/sync_hifleet_skills.py`
上游仓库：`https://github.com/charleiWang/hifleet-skills`

```bash
# 1. 查看当前本地受审版本
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py status

# 2. 只读比对上游 HEAD（不修改任何运行文件）
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py check

# 3. 审计候选版本（克隆到临时目录做全套校验，不成为 current）
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py prepare --revision <commit>

# 4. 启用已受审候选（原子更新 lock/manifest/SKILL.md/current，保留 last-known-good）
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py apply --revision <commit>

# 5. 校验一致性
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py verify

# 6. 回滚到 last-known-good 并重新校验
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py rollback
```

各命令语义：

| 命令 | 输入 | 行为 | 输出 |
| --- | --- | --- | --- |
| `status` | 无 | 读取 V2 lock | 本地版本/commit/哈希/last-known-good/批准与待审核能力 |
| `check` | 无 | `git ls-remote` 比对上游 HEAD，不改文件 | `NO_UPDATE` 或 `UPDATE_AVAILABLE` |
| `prepare` | `--revision` | 克隆候选到临时目录，做可信仓库/结构/语法/API Host/环境变量/能力识别/风险分类校验，报告写入 `docs/skills_v2/upstream-update-report.md`，候选暂存到 `candidates/` | 候选 JSON（`candidate_status: validated`）|
| `apply` | `--revision` | 只对 `validated` 候选执行原子更新，保留上一版本为 last-known-good；新增能力不自动进批准清单 | 更新结果 + `verify` 摘要 |
| `verify` | 无 | 校验 lock/manifest/SKILL.md/current 一致、批准能力有映射+Schema+测试、待审核未暴露、hifleet_data 无越权 | `verified` 或错误列表 |
| `rollback` | 无 | 从 `last_known_good/` 恢复实际内容（非仅版本号），lock 指向 lastKnownGood，重算 current，重新 verify | `rolled_back` + verify 结果 |

**安全保证**：
- `prepare` 候选**不会**直接成为 current。
- `apply` 任一步骤失败则全部回退到旧版本。
- `apply` 失败不改 current 与 last-known-good。
- 新增上游能力默认 `review_required`，必须先在 `capability_map.yaml` 配置且有契约测试才能转 `approved`。

### 5.2 capability_map 治理

`src/skills_v2/upstream/hifleet_skills/capability_map.yaml` 是能力暴露的治理清单。每项格式：

```yaml
- upstream_id: get_position        # 上游能力名
  local_tool: get_ship_position    # 本地工具名
  status: approved                 # approved 或 review_required
  read_only: true
  risk_level: medium
  input_schema: schemas/get_ship_position.input.json
  output_schema: schemas/get_ship_position.output.json
  adapter: skills_v2.skills.hifleet_data.adapter:get_ship_position
  contract_test: tests/skills_v2/hifleet_data/test_hifleet_data_capabilities.py
```

- **approved** 项必须有 `local_tool`、`adapter`、`input_schema`（文件存在）、`contract_test`，
  否则 `verify` 报错。
- **review_required** 项 `local_tool` 必须为空（不可暴露），否则 `verify` 报错。
- 注意处理上游与本地命名差异，例如 `get_avoidredsea_traffic` ↔ `get_avoid_redsea_traffic`。

**启用新上游能力的步骤**：
1. `prepare --revision <commit>` 审计候选，确认新能力出现在 `review_required_capabilities`。
2. 在 `capability_map.yaml` 新增 `approved` 项，补 `input_schema`/`output_schema` 文件。
3. 在 `src/skills_v2/skills/hifleet_data/manifest.yaml` 补 capability 映射。
4. 补契约测试（`contract_test` 指向的文件）。
5. `apply` → `verify` → 跑测试套件。

### 5.3 回滚

详见 [ROLLBACK.md](ROLLBACK.md)。两种场景：

- **V2 loader 失败**：自动进入 `safe_constrained`，无需手动操作。
- **上游版本回退**：`rollback` 命令恢复 last-known-good 实际内容并重新校验。

### 5.4 测试

```bash
# V2 核心测试（含边界、能力契约、registry、sync）
PYTHONPATH=src .venv/bin/python -m pytest tests/skills_v2 -q

# customer_ceshi 链路回归
PYTHONPATH=src .venv/bin/python -m pytest tests/customer_ceshi -q
PYTHONPATH=src .venv/bin/python -m pytest tests/customer_ceshi_v2 -q

# customer_support 只读保护回归（不依赖网络）
PYTHONPATH=src .venv/bin/python -m pytest tests/skills_v2/test_shared_registry.py tests/skills_v2/test_customer_support_shadow_graph.py tests/skills_v2/test_boundaries.py -q

# 上游一致性自检
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py verify
```

当前测试基线：`tests/skills_v2` 63 passed；`tests/customer_ceshi` 54 passed；
`tests/customer_ceshi_v2` 106 passed / 1 skipped / 7 xfailed。

### 5.5 自动维护检查

`docs/skills_v2/MAINTENANCE_PROMPT.md` 内含一段**固定维护提示词**，可直接交给 Agent
按 `status → check → prepare → 审查 → apply → verify → rollback → 测试` 流程自动检查上游更新。
该提示词的步骤与安全约束不可改写，只允许在执行结果区追加事实。

---

## 六、常见任务速查

| 任务 | 操作 |
| --- | --- |
| 修改 V2 工具实现 | 改对应 `src/skills_v2/skills/<skill>/adapter.py`，补 `tests/skills_v2/` 测试 |
| 修改 V2 Prompt | 改对应 `src/skills_v2/skills/<skill>/SKILL.md`（manifest 的 `prompt_file` 指向它） |
| 调整工具权限/风险 | 改 `manifest.yaml` 的 `read_only`/`risk_level`/`timeout_seconds`；`policy.py` 的 `DENIED_EXTERNAL_TOOLS` 控制全局禁用 |
| 临时切 customer_ceshi 回退 | `CUSTOMER_CESHI_SKILLS_MODE=legacy`（仍走 V2 safe_constrained，不加载 legacy Skills） |
| 检查上游是否有更新 | `sync_hifleet_skills.py check` |
| 升级 hifleet-skills 版本 | `prepare` → 审查报告 → `apply` → `verify` → 测试 |
| 确认边界未被破坏 | `pytest tests/skills_v2/test_boundaries.py -q` |
| 确认上游一致性 | `sync_hifleet_skills.py verify` |

---

## 七、相关文档

- [README.md](README.md) — V2 总览与快速入口
- [ARCHITECTURE.md](ARCHITECTURE.md) — 架构与边界细节
- [UPSTREAM_SYNC.md](UPSTREAM_SYNC.md) — 上游同步机制详解
- [MAINTENANCE_PROMPT.md](MAINTENANCE_PROMPT.md) — 固定维护提示词
- [ROLLBACK.md](ROLLBACK.md) — 回滚流程
- [TESTING.md](TESTING.md) — 测试命令与证据
- [CAPABILITY_MAPPING.md](CAPABILITY_MAPPING.md) — 能力映射说明
