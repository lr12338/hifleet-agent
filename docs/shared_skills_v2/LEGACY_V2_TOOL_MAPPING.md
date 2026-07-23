# Legacy / V2 Tool Mapping

| Legacy source | V2 contract | V2 availability |
| --- | --- | --- |
| `knowledge_qa.local_kb_search` | `knowledge_retrieval.local_kb_search` | External profiles |
| `knowledge_qa.web_search` | base `web_search` | External profiles; one entry only |
| `knowledge_qa.web_search_agent_browser` | none | Removed |
| `browser_verify.verify_public_page` | base `verify_public_page` | Only URL returned by current-runtime web search |
| `browser_verify.agent_browser_deep_search` | none | Removed |
| Read-only `hifleet_ship_service` tools | `hifleet_data` descriptors | External profiles after local manifest validation |
| `upload_ship_position` | internal adapter behind `commit_ship_update` | Never model-visible |
| `update_ship_static_info` | internal adapter behind `commit_ship_update` | Never model-visible |
| `knowledge_admin.upsert_local_kb_entry` | none | Never in external V2 profiles |
| customer_ceshi Draft tools | `ship_info_update` manifest descriptors | External profiles, confirmation-gated |

Legacy names and schemas remain unchanged for `customer_support=legacy`. V2 schema
changes are represented by the descriptor contracts and transaction manifests,
rather than silently renaming a legacy endpoint.
