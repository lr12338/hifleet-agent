# Shared Skills V2 Test and Validation Record

`tests/skills_v2/` covers manifests, duplicate names, mode defaults and rollback,
adapter contract equivalence, forbidden external tools, update validators, result
version metadata, and known-URL browser verification.

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/skills_v2
```

The `/run` and `/stream_run` implementation was audited in `src/main.py` and was
not changed. HTTP verification requires a separately configured non-production
service and model credentials; this task does not restart a service or send real
ship writes. See `HTTP_VALIDATION.md` for the accurate current status.

No live model latency baseline or attachment corpus is available in this workspace.
The five-case fixture is a semantic specification, not a claimed 5/5 live result.
Before customer_support promotion, run it over both chains and record legacy/V2
tools, evidence, claims, Draft states, and P95 agent orchestration time.
