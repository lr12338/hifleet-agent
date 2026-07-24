# Legacy / V2 工具映射

| Legacy 来源 | V2 契约 | V2 可用性 |
| --- | --- | --- |
| `skills.knowledge_qa.tools.local_kb_search` | `skills_v2.skills.knowledge_retrieval.adapter.local_kb_search` | 可用 |
| `skills.knowledge_qa.tools.web_search` | `skills_v2.skills.web_search.adapter.web_search` | 可用 |
| `skills.knowledge_qa.tools.web_search_agent_browser` | (拒绝) | 不可用 |
| `skills.browser_verify.tools.verify_public_page` | (拒绝) | 不可用 |
| `skills.browser_verify.tools.agent_browser_deep_search` | (拒绝) | 不可用 |
| `skills.hifleet_ship_service.tools.ship_search` | `skills_v2.skills.hifleet_data.adapter.ship_search` | 可用 |
| `skills.hifleet_ship_service.tools.get_ship_position` | `skills_v2.skills.hifleet_data.adapter.get_ship_position` | 可用 |
| `skills.hifleet_ship_service.tools.get_ship_archive` | `skills_v2.skills.hifleet_data.adapter.get_ship_archive` | 可用 |
| `skills.hifleet_ship_service.tools.get_psc_records` | `skills_v2.skills.hifleet_data.adapter.get_psc_records` | 可用 |
| `skills.hifleet_ship_service.tools.get_area_traffic` | `skills_v2.skills.hifleet_data.adapter.get_area_traffic` | 可用 |
| `skills.hifleet_ship_service.tools.get_strait_traffic` | `skills_v2.skills.hifleet_data.adapter.get_strait_traffic` | 可用 |
| `skills.hifleet_ship_service.tools.upload_ship_position` | `skills_v2.skills.ship_info_update.adapter.upload_ship_position` | 仅 ship_info_update 内部，模型不可直接调用 |
| `skills.hifleet_ship_service.tools.update_ship_static_info` | `skills_v2.skills.ship_info_update.adapter.update_ship_static_info` | 仅 ship_info_update 内部，模型不可直接调用 |

> V2 新增 7 个工具（get_areas + PSC openclaw 6 个）无 legacy 对应，为 V2 独有扩展。
