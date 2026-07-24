# Shared Skills V2 Test and Validation Record

`tests/skills_v2/` covers manifests, duplicate names, mode defaults and rollback,
adapter contract equivalence, forbidden external tools (including
`verify_public_page`), update validators, result version metadata, the
lock-anchored `source_versions`, the sync closed loop, mocked `/run` protocol
compatibility, and the customer_support opt-in dry-run shadow record.

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/skills_v2
```

`tests/customer_ceshi/` and `tests/customer_ceshi_v2/` cover the scenario
contracts, Claim Guard (negation/conflict/weak-relevance), ship-update Drafts and
confirmation, trajectory reverse/single-side/boundary handling, placeholder
rejection, and the native Responses/Chat runtime loop.

The `/run` and `/stream_run` implementation in `src/main.py` was not changed.
HTTP verification requires a separately configured non-production service and
model credentials; this task does not restart a service or send real ship
writes. See `HTTP_VALIDATION.md` for the accurate current status.

## Current counts (HEAD of `codex/shared-skills-v2`)

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/skills_v2 tests/customer_ceshi tests/customer_ceshi_v2
```

| Suite | passed | skipped | xfailed | failed |
| --- | --- | --- | --- | --- |
| `tests/skills_v2` | 35 | 0 | 0 | 0 |
| `tests/customer_ceshi` | 54 | 0 | 0 | 0 |
| `tests/customer_ceshi_v2` | 106 | 1 | 7 | 0 |
| **combined** | **195** | **1** | **7** | **0** |

The 7 `xfailed` items are obsolete Doubao-led media assertions (media is now
DeepSeek-led through `inspect_media`); they carry specific reasons and are not
masks for real failures. See `XFAIL_AUDIT.md`.

## Public regression runner statuses

`scripts/skills_v2/run_shared_skills_v2_regression.py` no longer reports only
`passed`/`failed`/`blocked`. Each case gets one of:

- `fixture_prepared` - a valid fixture exists but no serving URL was supplied;
- `invalid_fixture` - `fixture_quality: invalid`;
- `mock_only` - exercised against a mock, not real `/run`;
- `real_http_passed` - real `/run` succeeded with policy/budgets satisfied but
  no image-semantic verification (text case, or no structured assertions);
- `semantic_passed` - a real image travelled through `/run`/`/stream_run` into
  the model **and** the structured semantic assertions
  (`required_observations`, `required_uncertainty`, `forbidden_certainty`,
  `required_layer_distinctions`) were satisfied;
- `failed` - HTTP failure or a policy/semantic assertion failed;
- `blocked` - `contract_only` cases or a required attachment that is unavailable.

HTTP 200, an `inspect_media` call, or the absence of a fixed forbidden string
never count as `semantic_passed` on their own. `semantic_score` is required and
non-empty for every case; it is the human criterion encoded by the structured
assertions.

```bash
PYTHONPATH=src .venv/bin/python scripts/skills_v2/run_shared_skills_v2_regression.py \
  --base-url http://127.0.0.1:18128 \
  --attachment-base-url http://127.0.0.1:18080/fixtures \
  --report reports/shared_skills_v2/public-regression.json
```

M03/M05 carry `fixture_quality: valid` (the real HiFleet chart fixture); without
a serving URL they report `fixture_prepared`, not a pass. M01 is
`reference_only`. The controlled-evidence cases E09-E12 are `contract_only` and
remain `blocked` until their deterministic fixture service is supplied; they are
never counted as live semantic passes.

## Live results available in this workspace

No live model latency baseline or full attachment corpus is available in this
workspace. The 2026-07-23 isolated M02/M04/M05 probe (3 passed subset) and the
isolated `/run`/`/stream_run` HTTP samples in `HTTP_VALIDATION.md` remain the
most recent live evidence; they are not a 5/5 or corpus-wide acceptance result.
Before customer_support promotion, run the applicable cases over both chains and
record legacy/V2 tools, evidence, claims, Draft states, and P95 orchestration
time.

The V2-load failure regression forces manifest construction to fail and verifies
that the resulting `safe_constrained` runtime omits direct writes, knowledge
administration, `verify_public_page`, `web_search_agent_browser`, and
`agent_browser_deep_search`. This is a local deterministic fallback test, not a
production failure simulation.
