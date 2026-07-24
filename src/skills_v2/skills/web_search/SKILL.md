# 网页搜索 V2

仅使用 `web_search` 执行一次有界的公开网页搜索以获取候选证据。本 Skill 只暴露一个工具：
`web_search`。它不得打开、点击、导航或以任何方式操作网页，也不得包含
`verify_public_page`、`agent_browser_deep_search` 或 `web_search_agent_browser`。

每条结果必须保留 URL、标题、摘要和来源类型。弱相关、冲突或非官方来源仅作为低强度证据，
不得覆盖权威 HiFleet 数据，也不得作为确定的产品结论呈现。需遵守单轮调用次数、超时和
重复查询预算；通过收窄或改写查询来避免重复相同请求。
