# hifleet-skills Synchronization and Version Locking

The active upstream lock is `hifleet-skills` version `0.3.21`, commit
`e4acf599192f3f1d247ef2da00e78d0cff89819c`, recorded in `skills-lock.json`.
It was audited from a detached candidate clone on 2026-07-23. The upstream
declares `HIFLEET_API_KEY`, defaults to `https://api.hifleet.com`, and includes
account, billing, registration, console, and contact-unlock flows alongside
read-only data APIs. Those account and write-like capabilities are not exposed.

Run a non-mutating candidate audit:

```bash
python3 scripts/sync_hifleet_skills.py --revision HEAD
```

Use `--apply` only after reviewing candidate output and running contract and
regression tests. The script clones into a temporary candidate directory, checks
required files, records commit/content hash, and updates the lock only on explicit
`--apply`. A failure leaves the lock and last-known-good commit intact.
