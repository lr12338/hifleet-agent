# Dual-Chain Migration, Shadowing, and Rollback

## Current state

- `customer_support` remains `legacy` by default.
- `customer_ceshi` uses V2 when manifest loading succeeds; on V2 load failure it
  logs the reason and uses its existing constrained runtime.
- The customer_support adapter is shadow-only. A shadow write must stay Draft or
  dry-run and must never issue a duplicate low-level write.

## Shadow comparison

For the same customer_support request, retain the legacy user response and record
the V2 scenario, tools/parameters, evidence, guarded claims, Draft state, tool
count, and orchestration latency. Promotion remains internal account → 5% → 20%
→ 50% → 100%. This repository does not claim a production rollout happened.

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
