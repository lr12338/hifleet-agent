# Shared Skills V2 测试与验证记录

`tests/skills_v2/` 覆盖 manifest、工具去重、模式默认值、回滚、边界独立性、能力契约与上游同步。

## 测试套件

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_shared_registry.py` | 模式默认值、影子评估、共享契约、能力暴露限制、manifest 校验、写入校验器 |
| `test_customer_ceshi_v2_registry.py` | V2 bundle 构建、加载失败降级、lock 锚定版本、web_search 独立 Skill |
| `test_boundaries.py` | AST 边界扫描、customer_ceshi 只用 V2、customer_support 不用 V2、safe fallback、上游 apply 失败保护 |
| `test_hifleet_sync.py` | 候选审计、lock 更新、manifest 同步、SKILL.md 重生成、未审核能力保护 |
| `test_hifleet_data_capabilities.py` | 每个 approved 能力有 adapter 工具 + schema + 契约测试；review_required 不暴露 |
| `test_http_contract.py` | `/run` 响应含 V2 模式与上游 commit |
| `test_customer_support_shadow_graph.py` | 影子评估图构建与 trace 字段 |
| `test_public_regression_runner.py` | 公共语义回归用例校验与评估逻辑 |

## 运行命令

```bash
# V2 核心测试
PYTHONPATH=src .venv/bin/python -m pytest tests/skills_v2 -q

# customer_ceshi 链路回归
PYTHONPATH=src .venv/bin/python -m pytest tests/customer_ceshi -q
PYTHONPATH=src .venv/bin/python -m pytest tests/customer_ceshi_v2 -q

# customer_support 只读保护回归
PYTHONPATH=src .venv/bin/python -m pytest tests/skills_v2/test_shared_registry.py tests/skills_v2/test_customer_support_shadow_graph.py tests/skills_v2/test_boundaries.py -q

# 上游一致性自检
PYTHONPATH=src .venv/bin/python scripts/skills_v2/sync_hifleet_skills.py verify
```

## 当前基线（2026-07-24）

| 套件 | passed | skipped | xfailed | failed |
| --- | --- | --- | --- | --- |
| `tests/skills_v2` | 70 | 0 | 0 | 0 |
| `tests/customer_ceshi` | 54 | 0 | 0 | 0 |
| `tests/customer_ceshi_v2` | 106 | 1 | 7 | 0 |

> `tests/customer_ceshi_v2` 中 1 个 skipped 为需真实模型凭据的 smoke 测试；7 个 xfail 为已废弃的
> Doubao 主导媒体行为（详见 [XFAIL_AUDIT.md](XFAIL_AUDIT.md)），非真实失败。
