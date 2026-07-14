# HiFleet 多模态客服评测

## 方法与可核验产物

本次不是 mock 结果：对 M01–M05 使用配置的真实多模态模型进行了 base64 附件感知，并运行了本地真实 lightweight Graph。脱敏的原始结果保存在：

- `artifacts/multimodal_real_perception_trace.json`
- `artifacts/multimodal_fixture_graph_trace.json`
- `artifacts/m05_real_perception_retry.json`
- `artifacts/m05_real_perception_normalized.json`
- `artifacts/m05_real_perception_center_crop.json`
- `artifacts/m05_real_graph_trace.json`
- `artifacts/m04_current_direct_graph.json`

本地 Graph 已使用 `COZE_CHECKPOINTER_MODE=memory`，因为服务配置的 PostgreSQL checkpointer 需要运行中的 async loop。真实 Graph 路由完成，但本机工具注册未提供 `smart_search`，浏览器核验又被外部页面等待阻塞；因此本报告没有把缺失的检索或后台结果描述为已验证事实。

可复用的评测入口为：

```bash
.venv/bin/python scripts/eval_multimodal_customer_support.py --case all --output-json artifacts/multimodal_customer_support_eval_run.json --output-md artifacts/multimodal_customer_support_eval_run.md
```

默认是 `contract_only`，只检查 fixture、理解、场景、允许/禁止工具合同；它会对 M01–M05 标记 `not_executed`，不会伪造 live 结果。只有显式传入 `--direct-graph` 才会执行本地 Graph；M06–M15 会持续标记 `missing_fixture` 直至提供真实附件。

如果本地或灰度 `/run` 已部署，可改用 `--run-api`；脚本只读取 `MULTIMODAL_EVAL_API_URL` 和可选的 `MULTIMODAL_EVAL_API_TOKEN`，不保存 endpoint、令牌或原始附件 data URL。未配置时会记录 `api_not_configured`，不会静默切换为 mock。

## 真实案例结果

| Case | 修改前回复 | 修改后回复（实测或安全回复合同） | scenario / 路由 | 实测证据与工具序列 | 评分 / 延迟 / 失败项 |
|---|---|---|---|---|
| M01 | 未归档可复放的旧版生成回复；已知旧链路会把图片转写后交给普通 Agent。 | 仅确认“红色圆形、中心黑点”这一可见事实；无权威图例时说明不能确认含义，并请求一个图例/图层细节。 | `chart_symbol_explanation` → `chart_symbol` | 真实感知高置信：红色圆形、中心黑点咨询；Graph：`inspect_media_attachment` | 部分通过；27.9s。缺权威图例检索，未断言符号含义。 |
| M02 | 未归档可复放的旧版生成回复；线状符号没有专项处理。 | 说明用户指向紫色波浪线；感知低置信时请求更清晰区域或图例，而非猜测含义。 | `chart_symbol_explanation` → `chart_symbol` | 用户文字指向紫色波浪线；Graph：`inspect_media_attachment` | 部分通过；30.9s。感知低置信，要求图例/更清晰区域。 |
| M03 | 未归档可复放的旧版生成回复；UI OCR 容易盖过用户真正问题。 | 以用户的“小圈圈含义”为目标，不把 OCR 的 HiFleet 页面名当作问题；无图层/图例不固定解释为锚地。 | `chart_symbol_explanation` → `chart_symbol` | 真实 OCR 识别 HiFleet 页面和多个空心圈；用户文字优先修正 scenario | 部分通过；82.2s。缺图层/图例证据，不能把圈固定解释成锚地。 |
| M04 | 未归档可复放的旧版回复；初次本地 trace 曾按弱 FAQ 推导口径，现作为回归缺陷记录。 | 只确认截图的 `9.73 kn`、`4201 海里`与时间范围；无官方产品定义时明确不能确认停航/进出港是否计入。 | `platform_metric_definition` → `knowledge` | 当前真实 Graph：原图保留、OCR 提取 RAYHONG / MMSI `636024656` / IMO `9403073` / `9.73 kn` / `4201 海里`；工具序列 `local_kb_search → web_search → web_search_agent_browser` 重试三组；浏览器未验证时触发 `metric_definition_without_product_evidence` | 部分通过；236.2s。已验证不会把未核验搜索摘要当作产品口径；外部检索仍未取得可用官方正文。 |
| M05 | 未归档可复放的旧版生成回复；容易误进更新或普通问答。 | 创建只读 IncidentPacket：分船解析、标记“船端 AIS/周边船正常”为用户陈述、列出排查层级；不调用写工具。 | `ship_tracking_incident` → read-only incident chain | 当前真实 Graph 的通用左/中/右细节拼图识别 `GOLDEN LILY`（MMSI `370731000`、IMO `9216468`）和 `禾盛东方`；只调用 `get_ship_position`、`get_ship_archive`、`ship_search`。第二艘由 `ship_search` 补全为 MMSI `413228060`，再逐船读取。 | 部分通过；66.3s。本轮工具返回两船当前更新时间/状态，但与截图的 `2026-06-27` 历史显示不一致，已作为时间差异保留，不虚构后台根因。 |

M06–M15 的服务器 fixture 未提供，因此统一标记为 `missing_fixture`，未伪造测试结果；场景和回归测试框架已覆盖它们的路由原则。

为避免把框架覆盖误写成真实附件评测，M06–M15 另有 `synthetic_contract`：仅向理解层输入脱离文件名的文字和结构化感知摘要，验证预期 scenario、写入门禁、音频/视频的“媒体 envelope + 业务 scenario”分流。该覆盖不产生 OCR、船位或后台工具成功结论，真实 fixture 仍是上线前必需项。

其中语音转写属于客户自身表达，可作为“明确写入请求”的文字来源，但仍必须经过现有 ship-update 子 Agent、身份/字段校验与确认门禁；视频摘要只描述观察结果，不能单独授权写入。M15 还覆盖“真实文件 + 上传失败截图”的混合附件：当用户明确要求分析文件时，文件任务优先，解析工具只接收实际文件 URL；仅有上传失败截图时仍按平台排障处理。

新增 graph-level 回归验证：音频的 `recognized_text` 中出现完整船位更新命令时，轻量客服 Graph 必须进入 ship-update 子 Agent 的确认门禁，不能委派给 standard Agent，也不能在未确认时调用写工具。

## M04 平均航速结果

- **截图提取结果：** 平均航速 `9.73 kn`；总里程 `4201 海里`；轨迹范围 `2026-03-28 09:44` 到 `2026-06-26 09:44`。
- **产品定义证据：** 初次本地 Graph trace 只有无官方支持的本地 FAQ，曾输出了不可接受的口径断言；已修复为 metric-specific evidence guard，并添加回归测试。理解合同现将分母、停泊/锚泊、进出港低速和航次平均速度分别列为 required claims。最新真实 Graph 的 `web_search_agent_browser` 未能验证官方正文，且未核验的官方域名搜索摘要不会再算作产品定义证据；代码只确认截图值与时间范围。
- **停航和进出港：** 不能仅由“总里程/时间/平均航速”算术关系判断是否计入；无官方产品定义证据时代码明确回答为**待官方产品说明核验**。
- **未核实项：** 停泊、锚泊、静止、进出港低速，以及页面平均航速和航次平均速度是否为不同口径。

## M05 船位异常结果

- **两艘船身份：** 当前真实 Graph 的通用多区域细节感知读取到 `GOLDEN LILY`（MMSI `370731000`、IMO `9216468`）和 `禾盛东方`。第二艘的截图 MMSI/IMO 未显示，因此链路先用船名 `ship_search`，再补全为 MMSI `413228060`；该补全来自实际读取工具，不是 OCR 猜测。
- **工具查询结果：** 真实 Graph 只调用 `get_ship_position`、`get_ship_archive`、`ship_search`，没有调用写工具。工具返回 `GOLDEN LILY` 更新时间 `2026-07-13 18:45:56 UTC+8`、状态“锚泊”、航速 `0.1 节`；`禾盛东方` 更新时间 `2026-07-13 18:48:33 UTC+8`、状态“系泊”、航速 `0.1 节`。这些是工具返回的当前状态，不等同于截图中“无更新”的历史时间点。
- **异常层级：** 截图仍显示 `GOLDEN LILY` 的历史更新时间 `2026-06-27 05:44:25 UTC+8`；最新真实 Graph 工具返回 `2026-07-13 18:54:57 UTC+8`，并在客户回复中显式标注“截图与本轮工具时间差异”。IncidentPacket 仅把船端 AIS、接收覆盖、上游数据源、MMSI/IMO 映射、平台消费/过滤和单船配置列为候查层级，不声称已经诊断根因。
- **工单：** 建议基于截图时间点与当前工具回包的时间差异提交技术排查，分船比对历史最后报文、缓存/图层刷新和数据消费链路。
- **未验证项：** 截图时刻是否同步中断、覆盖/上游/平台链路状态，以及截图中的“无更新”是否来自旧缓存、历史回放或实际数据延迟。

## 自动化测试

```bash
.venv/bin/python -m pytest -q tests/test_customer_support_router.py tests/test_customer_support_intent_agent.py tests/test_multimodal_payloads.py tests/test_customer_support_p0_optimization.py tests/test_multimodal_customer_support_eval.py
```

结果：`208 passed`、`0 failed`、2 个第三方弃用警告（完整专项回归，含 M15 混合附件回归）。

## 修改前后指标

| 指标 | 修改前 | 修改后（当前可验证样本） |
|---|---:|---:|
| scenario 准确率 | 仅粗粒度图片/普通 Agent 路由 | M01/M02/M03/M04/M05 规则/真实理解路由正确；M03 已验证用户目标优先于 UI OCR |
| 有效工具选择率 | 多媒体被 `not has_multimodal_input` 排除在确定性知识链外 | 7 个必需专项 scenario 进入确定性链；M05 禁止写工具 |
| 裸拒答率 | 低置信图片可直接拒答 | 低置信回复包含已识别事实、不能确认原因和一个关键补充项 |
| 错误写入率 | 依赖后续门禁 | 当前专项 incident 测试为 0 写工具调用；M05 real Graph 为 0 |
| 无证据高置信率 | 图表符号可能依关键词模板给结论 | 无权威图例/产品证据时不下高置信结论 |
| 平均工具调用数 | 未留可比线上基线 | 本地 Graph M01–M05 平均 0.4；因 `smart_search` 注册缺失，不可视为生产指标 |
| 当前样本延迟 | 未采集 | 真实感知 27.9s–133.9s；Graph 5.7s–13.1s（退化工具环境） |

## 上线建议

**可本地测试，暂不建议生产全量上线。**

上线前需要：补齐 M06–M15 fixtures；为本地/灰度环境注册 `smart_search` 与船舶读取工具；为附件提供可访问 HTTP(S) URL 或统一 base64 适配；对 M04 建立官方产品口径证据；对 M05 完成两艘船实时工具核验和工单闭环。
