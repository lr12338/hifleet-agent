# hifleet-skills Synchronization and Version Locking

The active upstream lock is `hifleet-skills` version `0.3.21`, commit
`e4acf599192f3f1d247ef2da00e78d0cff89819c`, recorded in `src/skills_v2/upstream/hifleet_skills/lock.json`
(content hash `7118592b…0f8a`). It was audited from a detached candidate clone.
The upstream declares `HIFLEET_API_KEY`, defaults to `https://api.hifleet.com`,
and includes account, billing, registration, console, and contact-unlock flows
alongside read-only data APIs. Those account and write-like capabilities are not
exposed.

`src/skills_v2/upstream/hifleet_skills/lock.json` is the **single source of truth**. The validated candidate
discovers 16 scripts: 13 reviewed read-only data scripts are eligible for
explicit adapter mapping, while `charter_contact_dedup`,
`charter_enrich_helpers`, and `open_console` remain review-required and are not
automatically exposed.

## Closed-loop sync (candidate → review → snapshot → runtime → rollback)

`scripts/skills_v2/sync_hifleet_skills.py` implements the full chain without ever cloning
or executing upstream code during production requests:

1. **Candidate** — clone to a temporary directory at a fixed revision, record the
   commit, and validate trusted repository, required files, `SKILL.md`
   version/environment, HiFleet-owned API hosts, and Python syntax of upstream
   scripts. New scripts default to `review_required` and are never auto-exposed.
2. **Controlled snapshot** — on `--apply`, the same reviewed candidate updates
   three artifacts from one record:
   - `src/skills_v2/upstream/hifleet_skills/lock.json` (version, commit, `lastKnownGood`, `contentHash`,
     `approvedReadOnlyCapabilities`, `reviewRequiredCapabilities`, `requiredEnv`);
   - `src/skills_v2/skills/hifleet_data/manifest.yaml` (`skill_version`, `upstream_commit`
     kept in sync; the project-controlled adapter capability list and
     `upstream_capability` mapping are never auto-extended);
   - `src/skills_v2/skills/hifleet_data/SKILL.md` (regenerated with upstream provenance,
     approved/review-required capability lists, and the capability→adapter
     mapping).
3. **Runtime** — `SharedSkillRegistry` reads the lock through
   `upstream_lock_key: hifleet-skills` and overrides `skill_version`/
   `upstream_commit`; the adapter also carries `content_hash`/`last_known_good`
   in `source_versions`. Lock, manifest, prompt, and runtime metadata therefore
   never diverge.
4. **Rollback** — a validation failure returns the recorded last-known-good
   metadata without changing the lock; the runtime keeps using its checked-in
   local implementation.

Run a non-mutating candidate audit:

```bash
python3 scripts/skills_v2/sync_hifleet_skills.py --revision HEAD
```

Apply only after reviewing candidate output and running contract/regression tests:

```bash
python3 scripts/skills_v2/sync_hifleet_skills.py --revision HEAD --apply
```

Candidate discovery is deliberately non-enabling: adding a script upstream does
not add a model tool. A new capability requires an explicit manifest/adapter
contract review, tests, and a reviewed lock update before it can be mapped.
