# Shared Skills V2 Architecture

> 中文阅读入口与专题职责见 [README.md](README.md)。本文件说明实现边界；验收结论请只引用 [../customer_ceshi_acceptance_status.md](../customer_ceshi_acceptance_status.md)。

## Runtime boundary

`src/skills_v2/core/` is a new, manifest-driven layer. It does not modify or replace
`src/skills/skill_loader.py`; legacy callers keep their existing prompt and tool
loading behavior. `config/agent_profiles.json` controls runtime selection.

| Profile | Default mode | Adapter | User-visible behavior |
| --- | --- | --- | --- |
| `customer_support` | `legacy` | `skills_v2.adapters.customer_support_shadow` | Legacy customer reply remains primary; opt-in V2 shadow injects the shared Skill prompt into a no-tool assessment. |
| `customer_ceshi` | `v2` | `skills_v2.adapters.customer_ceshi` | Existing Responses/Chat runtime receives V2 descriptors, tools, and Skill instructions; V2 load failure uses the V2 safe_constrained fallback (never legacy skills). |

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

`web_search` and `inspect_media` are base capabilities, not business Skills.
customer_ceshi V2 exposes only `web_search` for public-web evidence; `verify_public_page`,
`agent_browser_deep_search`, and `web_search_agent_browser` are denied and are not
described to the model. Weak, conflicting, or non-official web results must be answered
conservatively or with a follow-up question; there is no browser tool to re-search.

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
| Shared validation | `prepare_ship_update` now invokes the shared V2 position/static validators before creating a Draft and returns `invalid_fields` instead of silently accepting malformed values. |
| API compatibility | `/run` and `/stream_run` remain in `src/main.py`; V2 changes no request or response field. |

## Single source of truth for upstream metadata

`src/skills_v2/upstream/hifleet_skills/lock.json` is the authoritative record for the `hifleet_data` upstream
version, commit, content hash, approved read-only capabilities, and required
environment. The `hifleet_data` manifest declares `upstream_lock_key: hifleet-skills`;
at runtime `SharedSkillRegistry` overrides `skill_version`/`upstream_commit` from the
lock, and the adapter also carries `content_hash`/`last_known_good` in
`source_versions`. `scripts/skills_v2/sync_hifleet_skills.py --apply` updates the lock, the
manifest snapshot, and `SKILL.md` from the same reviewed candidate, so lock, manifest,
prompt, and runtime metadata never diverge. New upstream capabilities are reported as
`review_required` and are never auto-added to the manifest or exposed to the agent.

## Observability

Normalized V2 results carry `skill_id`, `skill_version`, `upstream_commit`, and
`capability`. They are trace metadata, not proof that a natural-language claim is
semantically correct. Keys, cookies, tokens, and signed URLs are not recorded.
