# Agent-Browser 公开页面核验说明

本文只描述当前真实生效的 browser 核验能力。`agent-browser` 在本项目里不是自由浏览器 Agent，也不模拟登录或复杂点击流程；它是 `browser_verify` skill 下的受控公开网页证据层。

## 1. 当前链路

`customer_support` 当前主链：

```text
/run 或 /stream_run
-> profile=customer_support
-> 多模态感知
-> 需求理解 Agent 判断 route 和检索意图
-> planner / knowledge_qa / browser_verify 受控核验
-> customer output guard
```

触发 browser 的常见场景：

- 用户要求验证官网、帮助中心、官方社区、今日/最新内容。
- `local_kb_search` 或 `web_search` 只有弱命中、目录页、首页或候选链接。
- 需要核验具体公开页面正文，而不是只依赖搜索摘要。
- 需求理解和 planner 根据 `config/profiles/customer_support.md` 约束判断需要升级核验。

`customer_support_router.py` 中的 planner/harness 是当前受控执行层之一；历史轻量 delegate 链只作为回滚参考。

## 2. 工具边界

实现文件：[src/skills/browser_verify/tools.py](../src/skills/browser_verify/tools.py)

主要工具：

- `verify_public_page(url)`：核验单个公开 URL。
- `agent_browser_deep_search(query)`：生成候选链接，抓取公开页面正文，返回结构化 evidence。

`knowledge_qa` 中的 `web_search_agent_browser` 是桥接工具，用于把已锁定的候选 URL 或站点提示转交给 browser 核验。若 `web_search` 无有效命中但问题仍可能属于 HiFleet 平台/产品/社区/帮助内容，也可只传短关键词 `query`，由 browser 通过 Bing 优先寻找 HiFleet 官方候选页。

## 3. 候选与抓取策略

候选链接优先级：

1. HiFleet 官网、帮助中心、官方社区的具体页面。
2. Bing `site:hifleet.com` 和 `HiFleet + query` 召回的具体结果。
3. 公开可访问、与 query 高相关的其他页面。

抓取顺序：

```text
candidate url
-> agent-browser open
-> get title
-> get text body
-> 正文为空或明显不完整时 fallback 到 snapshot
-> 结构化 evidence JSON
```

正文、标题、URL、匹配理由和截图路径都是内部证据。最终回复由上层模型和 `sanitize_customer_output(...)` 生成，不直接展示原始 JSON、工具名、命令、内部路径或截图本地路径。

## 4. 安全限制

当前 browser 核验只允许公开网页：

- 仅处理 `http` / `https`。
- 禁止 `localhost`、`127.0.0.1`、`.local` 等本地地址。
- 不处理登录态，不传 Cookie，不读取浏览器日志。
- 不抓内部系统页面、管理后台、带 token 的私有链接。
- query 会做长度限制和注入字符过滤。
- 最终客户回复仍必须经过客服输出清洗。

## 5. Linux 部署注意

远端服务器先确认：

```bash
which agent-browser
agent-browser --help
```

如果 `agent-browser doctor` 或首次打开页面出现 `No usable sandbox`，建议在服务环境统一设置：

```bash
export AGENT_BROWSER_ARGS="--no-sandbox"
export AGENT_BROWSER_SESSION="hifleet-cs-fallback"
```

不要只在单条 `open` 命令上临时传 `--no-sandbox`，因为 daemon 重连或后续命令可能没有继承启动参数。

## 6. 单独验证

可以先在远端手工验证公开页面正文抓取：

```bash
agent-browser open "https://www.hifleet.com/wp/communities/fleet/haitutubiaoshuoming#post-305"
agent-browser wait --load networkidle || true
agent-browser get title
agent-browser get text body
```

然后再通过 `/run` 发送：

```json
{
  "messages": [{"role": "user", "content": "验证 注意！浏览器开始记忆船队“筛选”了 的详细内容"}],
  "session_id": "remote-smoke-browser-001",
  "user_id": "remote-dev",
  "source_channel": "websdk",
  "agent_profile": "customer_support"
}
```

预期：

- `route_trace.route` 通常为 `knowledge`、`browser_verify` 或 `chart_symbol`。
- `generated_tool_calls` 可看到 browser/knowledge 相关工具。
- 最终回复引用具体公开页面，不只给首页。
- 最终回复不出现 `agent-browser`、`reasoning_trace`、原始 JSON、内部路径或 key/token。

## 7. 排障清单

- 无法触发 browser：检查 `agent_profile=customer_support`、工具白名单、`browser_verify` skill 和模型是否选择了 browser 工具。
- browser 没有结果：检查服务器是否能访问 HiFleet 公开页面和 Bing，候选 URL 是否过于泛化。
- 回复暴露内部信息：检查 `sanitize_customer_output(...)`、`check_result` 和最终 answer 选择逻辑。
- 链接只是首页：检查候选生成是否召回具体文章/帮助页，必要时补充更具体 query。
- 截图/图片类问题缺证据：正文外可保留内部截图路径，但最终回复不要输出本地路径。
