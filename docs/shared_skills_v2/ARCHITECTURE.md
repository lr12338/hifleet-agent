# Shared Skills V2 Architecture

## Runtime boundary

`src/skills/core/` is a new, manifest-driven layer. It does not modify or replace
`src/skills/skill_loader.py`; legacy callers keep their existing prompt and tool
loading behavior. `config/agent_profiles.json` controls runtime selection.

| Profile | Default mode | Adapter | User-visible behavior |
| --- | --- | --- | --- |
| `customer_support` | `legacy` | `skills.adapters.customer_support` | Legacy only; V2 is shadow-ready but not used by its production chain. |
| `customer_ceshi` | `v2` | `skills.adapters.customer_ceshi` | Existing Responses/Chat runtime receives V2 descriptors, tools, and Skill instructions. |

`CUSTOMER_SUPPORT_SKILLS_MODE` and `CUSTOMER_CESHI_SKILLS_MODE` override the
mode without code changes. Invalid values fall back to safe defaults.

## Shared contracts

`ToolDescriptor` owns the tool name, JSON Schema, risk, timeout, confirmation
flag, Skill version, and upstream commit. The customer_ceshi adapter converts it
to Responses API JSON; the customer_support adapter exposes the same descriptors
for LangChain/LangGraph shadow evaluation. Protocol wrappers differ, business
contracts do not.

Only three business Skills exist in V2:

- `knowledge_retrieval`: internal `local_kb_search`, source-oriented evidence only.
- `hifleet_data`: verified, read-only HiFleet data capabilities.
- `ship_info_update`: transaction-level Draft tools; low-level writes are internal only.

`web_search`, `verify_public_page`, and `inspect_media` are base capabilities,
not business Skills. V2 exposes one web-search entry. customer_ceshi permits page
verification only for a URL returned by `web_search` during that runtime.

## Current audit record (2026-07-23)

Evidence was reviewed in `config/agent_profiles.json`, `src/skills/skill_loader.py`,
`src/agents/customer_support_router.py`, `src/agents/customer_ceshi_v2/tools.py`,
`src/agents/customer_ceshi_responses/builder.py`, `scenarios.py`, `ship_updates.py`,
and `claim_guard.py`.

| Audit item | Evidence-backed finding |
| --- | --- |
| Legacy load path | `SkillLoader._load_tools` hard-codes skill/tool lists and includes `upload_ship_position` and `update_ship_static_info`. |
| Prompt injection | `SkillLoader.build_full_prompt` reads `SKILL.md`; customer_ceshi previously used only profile prompts while assembling schemas separately. V2 appends manifest Skill prompts to the actual runtime profile prompt. |
| Duplicate lists | Loader `TOOL_MAP`, `CapabilityRegistry`, `READ_ONLY_TOOL_NAMES`, and scenario contracts overlap. V2 descriptors now supply V2 schemas; legacy lists remain fallback-only. |
| Search/browser overlap | `knowledge_qa` supplies `web_search` and deep search; `browser_verify` supplies verification and deep search. V2 excludes both deep-search variants. |
| Write safety | Existing customer_ceshi Draft/confirmation logic remains. V2 offers only transaction-level model tools. |
| API compatibility | `/run` and `/stream_run` remain in `src/main.py`; V2 changes no request or response field. |

## Observability

Normalized V2 results carry `skill_id`, `skill_version`, `upstream_commit`, and
`capability`. They are trace metadata, not proof that a natural-language claim is
semantically correct. Keys, cookies, tokens, and signed URLs are not recorded.
