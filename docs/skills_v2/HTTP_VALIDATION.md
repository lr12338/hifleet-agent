# `/run` 与 `/stream_run` 验证报告

> 说明（2026-07-24）：customer_ceshi V2 已收敛为 web_search + hifleet_data + ship_info_update 模式；
> `verify_public_page` 与 browser 工具已被拒绝。以下 2026-07-23 样本仍为本工作区最近的线上证据；
> 在无配置非生产服务与凭据的情况下无法执行新线上运行。

## 验证结论

| 验证项 | 结果 |
| --- | --- |
| `/run` 返回 V2 模式标记 | `metrics.skills_runtime.mode == "v2"` |
| 上游版本元数据 | `source_versions.hifleet_data.upstream_commit == "e4acf599"` |
| 工具调用与 schema | V2 描述符驱动，无 browser/写入越权 |
| `customer_support` 不受影响 | 默认 legacy，`/run` 请求/响应字段无变更 |

## 测试命令

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/skills_v2/test_http_contract.py -q
```

该测试验证 `/run` 响应中 `metrics.skills_runtime` 包含正确的 V2 模式与上游 commit。
