# Shared Skills V2 Test and Validation Record

`tests/skills_v2/` covers manifests, duplicate names, mode defaults and rollback,
adapter contract equivalence, forbidden external tools, update validators, result
version metadata, known-URL browser verification, mocked `/run` protocol
compatibility, and the customer_support opt-in dry-run shadow record.

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

Latest local evidence on 2026-07-23: `71 passed, 7 xfailed` for the focused V2
and customer_ceshi selection, plus `219 passed` for the protected
customer_support selection. A broader customer_ceshi invocation completed with
`174 passed, 1 skipped, 7 xfailed, 1 failed`; the single failure,
`test_standard_agent_success_claim_without_write_is_blocked`, reproduces unchanged
on `origin/main` at `333b2c156682dc2f978d113babe117b0a2824338` and is therefore
recorded as a pre-existing baseline failure, not a Shared Skills V2 regression.

For repeatable isolated HTTP validation, start a non-production process and run:

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_shared_skills_v2_http.py \
  --base-url http://127.0.0.1:18128 --profile customer_ceshi
```

The five-case public regression runner uses the same `/run` entry and records
`passed`, `failed`, or `blocked` without treating unavailable image files as a
pass. Supply an HTTP-served attachment directory for image cases:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_shared_skills_v2_regression.py \
  --base-url http://127.0.0.1:18128 \
  --attachment-base-url http://127.0.0.1:18080/fixtures \
  --report reports/shared_skills_v2/public-regression.json
```

The latest isolated M02 probe on 2026-07-23 used the plain public input
`HiFleet 平台上传不了航线。` and completed with one successful
`local_kb_search`, zero successful `web_search` calls, and a conservative
follow-up request. The runtime finalizes platform and membership replies from
direct internal evidence rather than exposing another search turn.

The public runner was rerun against an isolated current-worktree service on
2026-07-23 without attachment URLs: M02, M04, and M05 passed; M01 and M03 were
blocked as `attachment_url_not_supplied`; no case failed. This is a `3 passed,
2 blocked` partial result, not a semantic 5/5 acceptance result.
