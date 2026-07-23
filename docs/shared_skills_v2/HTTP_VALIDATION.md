# `/run` and `/stream_run` Validation Report

Status: **PASSED (isolated current-worktree service; safe no-write cases)**.

The request parser and profile resolution in `src/main.py` were audited. Existing
fields remain `messages`, `user_id`, `session_id`, `source_channel`, optional
`agent_profile`, and optional `llm_route`; V2 changed no request or response
schema. A configured non-production service must run on port `10123` before
executing regression requests. Do not send real ship writes.

On 2026-07-23, an isolated process started from this worktree on a temporary
non-production port. Safe `customer_ceshi` calls to `POST /run` and
`POST /stream_run` both returned HTTP 200. `/run` reported `status=success`,
`runtime_mode=responses`, one model call, no tools, `guard_result=not_required`,
and `skills_runtime.mode=v2`; its hifleet-data metadata reported upstream commit
`e4acf599192f3f1d247ef2da00e78d0cff89819c`. The measured end-to-end request
time was 3904 ms; streaming emitted three message events in 3735 ms. No tool or
write request was made.

An isolated `customer_support` request with `CUSTOMER_SUPPORT_SKILLS_SHADOW=true`
returned the legacy customer answer while server logs recorded
`completed_prompt_shadow`, `legacy_tools=6`, `v2_tools=20`,
`prompt_injected=True`, `write_state=no_shadow_write`, and 10,983 ms shadow
orchestration latency. The V2 shadow model received the injected shared Skill
prompt but was never bound to or allowed to execute tools; the external response
deliberately does not expose the full shadow record to customers. This is one
safe sample, not a P95 comparison or production rollout result.

Mock-only protocol regression on 2026-07-23 used FastAPI's `/run` entry with a
fake runtime. It proved unchanged request handling for `user_id`, `session_id`,
`messages`, `llm_route`, and profile selection, and verified that the V2 runtime
mode plus upstream version metadata are returned through the existing `metrics`
field. This is explicitly not a live model/provider validation.

For each request, record HTTP status, answer, effective profile/runtime, model and
tool calls/arguments, sources, Guard result, Draft state, elapsed time, timeout,
and legacy/V2 difference. Any fake-provider result must be labelled mock-only.
