# HiFleet 搜索工具真实环境测试报告（2026-07-13）

## 测试边界

- 测试时间：2026-07-13。
- 工作目录：本地 checkout；不打印 API Key、Token、Cookie 或请求头。
- 实测工具：`local_kb_search`、`web_search`、`web_search_agent_browser`，以及本机 `agent-browser` 可执行性。
- 脚本：`scripts/test_web_search_live.py`。

## 环境探测

| 项目 | 结果 |
| --- | --- |
| `agent-browser` | 已安装，版本 `0.27.0` |
| `ark_websearch_api_key` / 同类结构化搜索凭据 | 项目 `.env` 已配置；脚本通过 Python 安全加载，不回显值 |
| Ark fallback 运行所需身份与 base URL | 项目 `.env` 已配置；本次 structured-search 主路径成功，未触发 Ark 成功 fallback |

## 真实联网结果

| Query | local_kb_search | web_search | web_search_agent_browser |
| --- | --- | --- | --- |
| `HiFleet 筛选船队记忆功能` | 3 条、不可直接答 | `unavailable`（主接口与 Ark 均无配置） | 约 58.5s；仅目录页，修复后不可答 |
| `HiFleet CCTV GB28181 接入价格异常` | 3 条、不可直接答 | 3 条官方候选，约 0.3–1.1s，尚不可直接答 | 约 57.2s；仅目录页，修复后不可答 |
| `怎么绘制区域标注` | 2 条、可答 | 3 条、不可直接答 | 约 40.6s；仅首页/目录，修复后不可答 |

说明：第二、第三项 `web_search` 有时返回本地/缓存的可用结果，因此状态为 `ok`；第一项在本次运行中触发双配置缺失，修复后明确为 `unavailable`，不再抛给上层非结构化异常。

## Browser 实测结论

- CLI 可真实启动并访问公开网页；不是 mock。
- 无 target URLs 的 keyword fallback 在主工具路径可达。
- 本轮候选最终抓到的是官网首页或社区目录，不是可引用的具体文章；修复前错误标记 `can_answer=true`，修复后统一为 `generic_or_irrelevant_page` 和 `can_answer=false`。
- 因此本环境 browser 的“有效具体证据成功率”为 0/3；这反映候选质量/抓取结果不足，不能被包装成搜索成功。

## Mock / 单元测试

- 修改前全回归：`167 passed`。
- 新增覆盖：技术限定词保真、私网 DNS 阻止、重定向 SSRF 阻止、browser 不可用状态、目录页拒绝。
- 最终完整回归命令：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_smart_search_tools.py \
  tests/test_customer_support_router.py \
  tests/test_customer_support_intent_agent.py
```

## 被跳过或受环境限制的验证

- 已验证真实 structured-search 与本地 `block_hosts` 后过滤；远端服务是否原生执行 `BlockHosts` 不以 trace 猜测，当前本地过滤保证不会返回被屏蔽 host。
- 未触发可成功的 Ark fallback，因而不能给出 Ark 成功率。
- 已执行当前 worktree 的 `/run` deterministic trace；由于端口/代理归属冲突，最终一次独立服务重启未用于重复验证保守 fallback。该修复由回归测试覆盖，正式部署重启后仍应复跑。
- 三个 live query 样本不足以形成稳定 Precision@3 和 p50/p95 基线。

## 复测步骤

1. 在部署环境仅配置所需合法凭据，不在终端或报告回显其值。
2. 运行 `PYTHONPATH=src .venv/bin/python scripts/test_web_search_live.py > /tmp/hifleet-live.json`。
3. 检查三类查询：官方具体文章、CCTV/GB28181 技术限定词、教程入口/步骤/完成条件。
4. 再调用 `/run`，确认 retrieval trace 包含结构化状态、候选 URL、browser 结果和 evidence review，且客户文本不含内部实现细节。

## 追加真实验证（2026-07-13）

### Browser 具体页面

- 显式 URL：`https://www.hifleet.com/wp/communities/smartship/hifleetshangxianzhinengshipinjiankongxitong`
- 实测结果：`status=ok`、`can_answer=true`、1 个相关具体页面。
- 页面证据：`specific_page=true`、`body_quality=good`、`query_term_coverage=0.5`、`fact_evidence_count=1`、`step_evidence_count=2`、`can_support_answer=true`。

### Browser 无 URL fallback

- 输入：`HiFleet 船舶智能视频监控 产品介绍`，`target_urls=""`，`site_hint="hifleet.com"`。
- 实测结果：`status=ok`、`can_answer=true`、原始候选/页面 2 个、可引用相关页面 1 个。
- 结论：无 target URL 的关键词 fallback 在实际工具路径可用。

### `/run` deterministic trace

- 使用独立当前-worktree 服务实例，`evidence_required` CCTV/GB28181 场景实测进入 `route=knowledge` 与 `understanding_to_knowledge_chain`。
- 工具序列覆盖 `local_kb_search → web_search → web_search_agent_browser`，并因 browser 无关页继续 4 个查询。
- trace 记录：`t2_attempted=true`、`t2_status=browser_irrelevant_page`、`t2_can_answer=false`、`t2_relevant_page_count=0`、`query_trace_count=4`。
- 客户文本未暴露 tool 名、Bing、agent-browser、JSON、路径、Key 或错误堆栈。

### 已知运行环境限制

- 独立服务端口的后续重启发现端口/代理归属冲突，未把该问题归因于检索实现；原持久 `10123` 服务进程并非本次修改后启动。
- 正式部署应重启目标服务后，复跑本报告脚本和 `/run` CCTV/GB28181 场景，确认保守 fallback 生效。
