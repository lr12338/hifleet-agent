# `/run` and `/stream_run` Validation Report

Status: **PARTIAL: live safe smoke passed; V2 deployment identity not observable**.

The request parser and profile resolution in `src/main.py` were audited. Existing
fields remain `messages`, `user_id`, `session_id`, `source_channel`, optional
`agent_profile`, and optional `llm_route`; V2 changed no request or response
schema. A configured non-production service must run on port `10123` before
executing regression requests. Do not send real ship writes.

On 2026-07-23, `GET /health` returned HTTP 200. Safe `customer_ceshi` calls to
`POST /run` and `POST /stream_run` also completed successfully: `/run` returned
`status=success`, and streaming emitted `message_start`, `answer`, and
`message_end` with a reported `time_cost_ms=1883`. The response did not expose an
effective Skills mode, upstream commit, tool trace, Guard result, or Draft status,
so this is a real endpoint smoke test, not proof that the running process loaded
this worktree's V2 code. No tool or write request was made.

For each request, record HTTP status, answer, effective profile/runtime, model and
tool calls/arguments, sources, Guard result, Draft state, elapsed time, timeout,
and legacy/V2 difference. Any fake-provider result must be labelled mock-only.
