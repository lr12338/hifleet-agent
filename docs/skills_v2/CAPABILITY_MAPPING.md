# Shared Skills V2 能力映射

customer_ceshi V2 仅暴露以下工具。Browser/页面验证/深度搜索/知识库管理/底层写入均被拒绝。

## knowledge_retrieval（只读本地知识库）

| 工具 | 上游能力 | 风险 | 说明 |
| --- | --- | --- | --- |
| `local_kb_search` | (项目) | medium | 检索本地知识库，返回证据 ID/来源/匹配度/摘要 |

## web_search（单次公开网页搜索）

| 工具 | 上游能力 | 风险 | 说明 |
| --- | --- | --- | --- |
| `web_search` | (项目) | low | 单次网页搜索，保留 URL/标题/摘要/来源类型 |

## hifleet_data（只读 HiFleet 数据，21 个工具）

| 工具 | 上游能力 | 风险 | 说明 |
| --- | --- | --- | --- |
| `ship_search` | (项目) | low | 按关键字搜索船舶 |
| `get_ship_position` | get_position | medium | 查询船舶实时位置 |
| `get_ship_archive` | get_archive | medium | 查询船舶档案 |
| `get_psc_records` | get_psc | medium | 查询 PSC 检查记录 |
| `get_area_traffic` | get_area_traffic | medium | 查询区域船舶数量 |
| `get_strait_traffic` | get_strait_traffic | medium | 查询海峡通航统计 |
| `get_ship_trajectory` | (项目) | medium | 查询历史轨迹点 |
| `get_ship_call_ports` | (项目) | medium | 查询历史挂靠记录 |
| `get_ship_voyages` | (项目) | medium | 查询历史航次 |
| `get_last_departure` | (项目) | medium | 查询最近一次离港 |
| `get_current_stop` | (项目) | medium | 查询当前停船 |
| `get_avoid_redsea_traffic` | get_avoidredsea_traffic | medium | 查询红海绕航每日统计 |
| `search_ports` | get_port | low | 检索港口列表 |
| `get_port_detail` | get_port | medium | 查询港口详情 |
| `get_areas` | get_areas | low | 查询所有可用区域清单 |
| `get_psc_anomalies` | get_psc_anomalies | medium | 查询 PSC 统计异常列表 |
| `get_psc_anomaly_summary` | get_psc_anomalies | medium | 查询 PSC 异常按严重度汇总 |
| `get_psc_anomaly_detail` | get_psc_anomalies | medium | 查询 PSC 异常单条详情 |
| `get_psc_stats_compare` | get_psc_openclaw_stats | medium | 查询 PSC 宏观区间对比 |
| `get_psc_defects_top` | get_psc_openclaw_stats | medium | 查询 PSC 缺陷码 Top 排行 |
| `get_psc_stats_mix_compare` | get_psc_openclaw_stats | medium | 查询 PSC 旗国/检查类型占比对比 |

## ship_info_update（两阶段确认写入）

| 工具 | 风险 | 需确认 | 说明 |
| --- | --- | --- | --- |
| `prepare_ship_update` | high | 是 | 生成并校验更新草稿 |
| `commit_ship_update` | high | 是 | 同会话确认后提交 |
| `cancel_ship_update` | medium | 是 | 取消当前草稿 |

## 拒绝暴露的上游能力

| 上游能力 | 状态 | 原因 |
| --- | --- | --- |
| `open_console` | review_required | 控制台换票，高风险 |
| `charter_contact_dedup` | review_required | 联系人去重，写入类 |
| `charter_enrich_helpers` | review_required | 租船信息增强，写入类 |
