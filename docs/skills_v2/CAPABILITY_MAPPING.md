# Shared Skills V2 Capability Mapping

customer_ceshi V2 exposes only the tools below. Browser/page-verification
capabilities (`verify_public_page`, `agent_browser_deep_search`,
`web_search_agent_browser`) are denied and not described to the model.
`customer_support` legacy retains its own browser capability unchanged.

## Foundation (non-business) tools

| Tool | V2 availability | Notes |
| --- | --- | --- |
| `web_search` | exposed | One public-web evidence entry; weak/conflicting results answered conservatively. |
| `inspect_media` | exposed | DeepSeek-led media perception; never a business tool loop. |
| `verify_public_page` | denied | Removed from customer_ceshi V2. |
| `agent_browser_deep_search` | denied | Removed. |
| `web_search_agent_browser` | denied | Removed. |

## Business Skills

| Skill | Tools | Confirmation | Source of truth |
| --- | --- | --- | --- |
| `knowledge_retrieval` | `local_kb_search` | read-only | local manifest |
| `hifleet_data` | 14 read-only adapter tools | read-only | `src/skills_v2/upstream/hifleet_skills/lock.json` (hifleet-skills) |
| `ship_info_update` | `prepare_ship_update`, `commit_ship_update`, `cancel_ship_update` | all require confirmation | local manifest + shared validators |

## hifleet_data adapter -> upstream capability mapping

Approved upstream read-only capabilities (13): `get_archive`, `get_area_traffic`,
`get_areas`, `get_avoidredsea_traffic`, `get_casualty`, `get_maritime_penalty`,
`get_port`, `get_position`, `get_psc`, `get_psc_anomalies`,
`get_psc_openclaw_stats`, `get_sanction`, `get_strait_traffic`.
Review-required (never exposed): `charter_contact_dedup`,
`charter_enrich_helpers`, `open_console`.

| adapter tool | upstream capability | kind |
| --- | --- | --- |
| `ship_search` | - | project adapter |
| `get_ship_position` | `get_position` | upstream-approved |
| `get_ship_archive` | `get_archive` | upstream-approved |
| `get_psc_records` | `get_psc` | upstream-approved |
| `get_area_traffic` | `get_area_traffic` | upstream-approved |
| `get_strait_traffic` | `get_strait_traffic` | upstream-approved |
| `get_ship_trajectory` | - | project adapter |
| `get_ship_call_ports` | - | project adapter |
| `get_ship_voyages` | - | project adapter |
| `get_last_departure` | - | project adapter |
| `get_current_stop` | - | project adapter |
| `get_avoid_redsea_traffic` | `get_avoidredsea_traffic` | upstream-approved |
| `search_ports` | `get_port` | upstream-approved |
| `get_port_detail` | `get_port` | upstream-approved |

"project adapter" marks HiFleet-API-backed tools this project reviews and exposes
directly; they are not auto-derived from upstream scripts. A new upstream
capability stays review-required until explicitly mapped here and in
`src/skills_v2/skills/hifleet_data/manifest.yaml`.

## Denied for external V2 profiles

`upload_ship_position`, `update_ship_static_info` (internal behind
`commit_ship_update`), `upsert_local_kb_entry`, `verify_public_page`,
`agent_browser_deep_search`, `web_search_agent_browser`,
`download_public_file_to_artifact`, `run_sandboxed_python`,
`upload_customer_artifact`.
