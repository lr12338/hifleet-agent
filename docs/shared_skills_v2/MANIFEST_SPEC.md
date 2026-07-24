# Shared Skills V2 Manifest Specification

Each V2 Skill has `manifest.yaml` with `schema_version: 1`, `skill_id`,
`skill_version`, `prompt_file`, and non-duplicate `capabilities`.

Each capability declares `id` (and optional `tool_name`), `description`,
`read_only`, `risk_level`, `timeout_seconds`, and, for transaction tools,
`requires_confirmation` plus an object `input_schema`. Upstream-backed manifests
also record `upstream_repository`, `upstream_commit`, and `upstream_lock_key` (the
`skills-lock.json` key that is the runtime authority for version/commit). Each
upstream-backed capability may declare `upstream_capability` mapping the adapter tool
to the reviewed upstream script (empty for project adapter extensions).

The loader rejects malformed YAML, missing capabilities, duplicate names,
non-object schemas, unsupported schema versions, and writable capabilities that
do not require confirmation. External V2 profiles are denied `knowledge_admin`,
`upload_ship_position`, and `update_ship_static_info` even if an accidental
manifest entry is added.
