# Dual-Chain Migration, Shadowing, and Rollback

## Current state

- `customer_support` remains `legacy` by default.
- `customer_ceshi` uses V2 when manifest loading succeeds; on V2 load failure it
  logs the exception class and uses a `legacy_constrained` runtime. That fallback
  still denies direct writes, knowledge administration, and both autonomous
  browser-search tools and `verify_public_page`; only `web_search` remains for
  public-web evidence.
- The customer_support adapter is shadow-only. Set
  `CUSTOMER_SUPPORT_SKILLS_SHADOW=true` to record a V2 comparison while the legacy
  graph continues producing the customer-visible answer. When the existing text
  model is available, the shadow receives the shared V2 Skill prompt and produces
  one no-tool JSON assessment; otherwise it records the contract-only fallback.
  A shadow write is always dry-run-only and never invokes a duplicate low-level
  write.

## Shadow comparison

For the same customer_support request, retain the legacy user response and record
the V2 scenario, allowed tools, legacy-only tools, evidence count, high-risk
success claim indicator, reply length, source versions, write state, tool count,
and orchestration latency. A prompt-backed record additionally includes the V2
scenario, filtered recommended tools, parameter/evidence requirements, confidence,
and proposed-reply risk indicators. It never binds or executes tools; parameters
and evidence are not replayed, so a shadow run cannot duplicate reads or writes.
Promotion remains internal account → 5% → 20% → 50% → 100%. This repository does
not claim a production rollout.

## Rollback

Set `CUSTOMER_CESHI_SKILLS_MODE=legacy` (or the `skill_runtime` mode in
`config/agent_profiles.json`) and restart through the normal deployment procedure.
For customer_support retain or set `CUSTOMER_SUPPORT_SKILLS_MODE=legacy`. Backup
tag `skills-baseline-20260723` and branch `backup/skills-v1-20260723` point to
the original main commit. No API client contract change is required.

## Ship-update confirmation

The model can prepare, commit, or cancel only a session-bound Draft. Invalid
fields remain in `invalid_fields`; they cannot be silently dropped. Position
updates require a nine-digit MMSI, longitude, latitude, and explicit
`yyyy-MM-dd HH:mm:ss` timestamp. Static updates require MMSI plus one update
field. A `ship_type`/`minotype` conflict blocks commit. Only `success` may be
described as “更新成功”.
