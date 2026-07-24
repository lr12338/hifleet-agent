# Shared Skills V2 Manifest 规范

每个 V2 Skill 包含 `manifest.yaml`，字段如下：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | int | 固定 `1` |
| `skill_id` | string | Skill 标识，须与目录名一致 |
| `skill_version` | string | Skill 版本（hifleet_data 运行时由 lock 覆盖） |
| `prompt_file` | string | Prompt 文件名，默认 `SKILL.md` |
| `upstream_repository` | string | 上游仓库 URL（仅 hifleet_data） |
| `upstream_commit` | string | 上游 commit（运行时由 lock 覆盖） |
| `upstream_lock_key` | string | lock 键名（仅 hifleet_data 为 `hifleet-skills`） |
| `capabilities` | list | 能力列表，每项含 `id`/`tool_name`/`description`/`read_only`/`risk_level`/`timeout_seconds` |

## 能力字段

| 字段 | 说明 |
| --- | --- |
| `id` | 能力标识 |
| `tool_name` | 对应的 adapter 工具名（事务工具可省略，用 `id`） |
| `upstream_capability` | 上游能力名（项目适配器为空） |
| `read_only` | 是否只读 |
| `requires_confirmation` | 是否需要确认（写入工具必须为 `true`） |
| `risk_level` | 风险等级：low/medium/high/critical |
| `timeout_seconds` | 超时秒数 |
| `input_schema` | 内联 JSON Schema（仅事务工具） |

manifest_loader 校验：schema_version=1、skill_id 匹配、capability 无重名、写入能力须声明 `requires_confirmation`。
