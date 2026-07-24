# config/skills_v2/

V2 runtime configuration artifacts. Active `skill_runtime` modes are resolved by
`src/skills_v2/core/policy.py` from `config/agent_profiles.json` plus the env
overrides below; `skill_runtime.json` documents the V2 defaults and V2-only
settings.

| Profile | Default | Env override | Fallback |
| --- | --- | --- | --- |
| `customer_ceshi` | `v2` | `CUSTOMER_CESHI_SKILLS_MODE` | `safe_constrained` (never legacy skills) |
| `customer_support` | `legacy` | `CUSTOMER_SUPPORT_SKILLS_MODE` | stays legacy; shadow via `CUSTOMER_SUPPORT_SKILLS_SHADOW` (off) |
