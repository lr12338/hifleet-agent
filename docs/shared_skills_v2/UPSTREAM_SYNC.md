# hifleet-skills Synchronization and Version Locking

The active upstream lock is `hifleet-skills` version `0.3.21`, commit
`e4acf599192f3f1d247ef2da00e78d0cff89819c`, recorded in `skills-lock.json`.
It was audited from a detached candidate clone on 2026-07-23. The upstream
declares `HIFLEET_API_KEY`, defaults to `https://api.hifleet.com`, and includes
account, billing, registration, console, and contact-unlock flows alongside
read-only data APIs. Those account and write-like capabilities are not exposed.

The latest audit also records the full candidate content hash, the required
environment variables, HiFleet-owned API/documentation hosts, and static Python
contract checks in `skills-lock.json`. The validated candidate discovers 16
scripts: 13 reviewed read-only data scripts are eligible for explicit adapter
mapping, while `charter_contact_dedup`, `charter_enrich_helpers`, and
`open_console` remain review-required and are not automatically exposed.

Run a non-mutating candidate audit:

```bash
python3 scripts/sync_hifleet_skills.py --revision HEAD
```

Use `--apply` only after reviewing candidate output and running contract and
regression tests. The script clones into a temporary candidate directory, checks
the trusted repository, required files, `SKILL.md` version and environment
variables, HiFleet-owned API hosts, and Python syntax for upstream scripts. It
records a full-tree content hash and updates the lock only on explicit `--apply`
after a validated result. A validation failure returns the recorded
last-known-good metadata without changing the lock; the runtime continues using
its already checked-in local implementation rather than cloning or pulling at
startup.

Candidate discovery is deliberately non-enabling: adding a script upstream does
not add a model tool. A new capability requires an explicit manifest/adapter
contract review, tests, and a reviewed lock update.
