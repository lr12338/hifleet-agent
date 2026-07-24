# customer_support 知识库运维手册

本手册只面向内部知识运营和开发人员，描述本地客服知识库的内容来源、授权更新和验证方式。它不是对外 API，也不是产品规则的唯一来源；任何面向客户的高风险结论仍必须以当前检索证据为准。

## 内容位置与职责

| 路径 | 用途 | 修改要求 |
| --- | --- | --- |
| `docs/RAG/hifleet_cs_wiki/` | 主题型客服知识与说明 | 保留来源、更新时间和不确定性 |
| `docs/RAG/hifleet_cs_outputs/` | FAQ、标准话术、检索词和结构化样本 | 作为检索/运营输入，不能把话术当作未核验产品事实 |
| `docs/RAG/update/` | 受控更新辅助材料 | 仅在授权流程中使用 |
| `src/skills/knowledge_qa/` | 本地检索实现 | 修改后必须验证检索结果和 Claim Guard |
| `src/skills/knowledge_admin/` | 授权写入实现 | 不得暴露给外部 V2 Profile |

知识库导航见 [RAG/hifleet_cs_wiki/INDEX.md](RAG/hifleet_cs_wiki/INDEX.md) 与 [RAG/hifleet_cs_outputs/INDEX.md](RAG/hifleet_cs_outputs/INDEX.md)；通用排查见 [DEVELOPER_TROUBLESHOOTING.md](DEVELOPER_TROUBLESHOOTING.md)。

## 授权更新边界

- 外部客户请求不能直接写入知识库。
- `knowledge_admin.upsert_local_kb_entry` 仅接受明确的内部更新意图和受控授权；不要在日志、Issue 或文档中记录授权 key。
- `customer_ceshi` Shared Skills V2 与 `customer_support` V2 shadow 都不暴露知识库管理工具。
- 更新内容必须能追溯来源、适用范围和更新时间；会员权限、套餐、上传格式、操作入口等高风险内容必须提供明确证据。

## 建议流程

1. 收集反馈与原始证据，先确认这是知识错误而非工具、路由或 Prompt 问题。
2. 修改对应 RAG 文档或受控结构化条目，避免复制相互矛盾的话术。
3. 用 `local_kb_search` 检查能否命中新增证据，并检查返回来源与版本。
4. 对高风险 Claim 运行 Claim Guard 相关测试；没有足够证据时保留保守回复或追问。
5. 在变更说明中记录资料来源、影响主题、测试结果和回滚方式。

## 验证命令

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/customer_ceshi/test_claim_guard.py \
  tests/customer_ceshi_v2/test_responses_runtime.py
```

如修改检索实现或 Shared Skills V2 manifest，再执行 [shared_skills_v2/TESTING.md](shared_skills_v2/TESTING.md) 中的完整 focused 选择。生产环境更新前必须遵循正常部署和审计流程，不在运行进程中手工修改知识库文件。
