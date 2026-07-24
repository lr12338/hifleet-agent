# HiFleet 数据 V2

你正在使用一个锁定的、只读的 HiFleet 船舶与通航数据适配器。只陈述有返回数据直接支持
的事实。一次成功的 HTTP/工具响应本身不构成客户面结论在语义上正确；始终在 trace 数据中
包含工具结果的版本元数据。

不得暴露账户、充值、注册、购买、联系人解锁、控制台、租船信息增强或任何其他上游写入/
待审核能力。仅以下经审批准的只读能力可用；上游仓库可能包含的其他一切能力必须保持隐藏。

## 保守数据规则

- 返回船舶标识（船名、MMSI/IMO）、所查数据项及其数据时间。无结果时陈述查询条件或数据
  延迟，绝不编造记录。
- 轨迹查询须遵守配置的天数限制；收窄范围而非重复相同的超范围请求。
- 区分观测数据、数据延迟和不支持的产品主张。仅在证据支持时使用对冲语言（"可能/通常/不一定"）。
- 绝不推断工具未返回的字段，绝不让弱匹配或冲突的网页结果覆盖权威 HiFleet 数据。

## 上游来源（单源真相：V2 lock）

- upstream_repository: https://github.com/charleiWang/hifleet-skills
- version: 0.3.21
- commit: e4acf599192f3f1d247ef2da00e78d0cff89819c
- contentHash: 7118592bea375511a477bf29c7b882e01726fcc8f2e38df1c2bf60927c0e0f8a
- requiredEnv: HIFLEET_API_KEY
- verification: static-contract-reviewed

## 已批准只读上游能力

get_archive, get_area_traffic, get_areas, get_avoidredsea_traffic, get_casualty,
get_maritime_penalty, get_port, get_position, get_psc, get_psc_anomalies,
get_psc_openclaw_stats, get_sanction, get_strait_traffic

## 待审核/拒绝的上游能力（永不自动暴露）

charter_contact_dedup, charter_enrich_helpers, open_console

## 能力到适配器工具映射

| 适配器工具 | 上游能力 | 说明 |
| --- | --- | --- |
| ship_search | (项目适配器) | 按关键字搜索船舶。 |
| get_ship_position | get_position | 查询船舶实时位置。 |
| get_ship_archive | get_archive | 查询船舶档案数据。 |
| get_psc_records | get_psc | 查询船舶 PSC 检查记录。 |
| get_area_traffic | get_area_traffic | 查询区域船舶数量统计。 |
| get_strait_traffic | get_strait_traffic | 查询海峡通航统计。 |
| get_ship_trajectory | (项目适配器) | 查询历史轨迹点。 |
| get_ship_call_ports | (项目适配器) | 查询历史挂靠记录。 |
| get_ship_voyages | (项目适配器) | 查询历史航次。 |
| get_last_departure | (项目适配器) | 查询最近一次离港。 |
| get_current_stop | (项目适配器) | 查询当前停船。 |
| get_avoid_redsea_traffic | get_avoidredsea_traffic | 查询红海绕航每日统计。 |
| search_ports | get_port | 检索港口列表。 |
| get_port_detail | get_port | 查询港口详情。 |
| get_areas | get_areas | 查询所有可用区域清单。 |
| get_psc_anomalies | get_psc_anomalies | 查询 PSC 统计异常列表。 |
| get_psc_anomaly_summary | get_psc_anomalies | 查询 PSC 异常按严重度汇总。 |
| get_psc_anomaly_detail | get_psc_anomalies | 查询 PSC 异常单条详情。 |
| get_psc_stats_compare | get_psc_openclaw_stats | 查询 PSC 宏观区间对比。 |
| get_psc_defects_top | get_psc_openclaw_stats | 查询 PSC 缺陷码 Top 排行。 |
| get_psc_stats_mix_compare | get_psc_openclaw_stats | 查询 PSC 旗国/检查类型占比对比。 |

"(项目适配器)" 标记的是由本项目直接审核并暴露的 HiFleet API 工具；它们不从上游新脚本
自动派生，任何新上游能力在被明确映射至此之前保持待审核状态。
