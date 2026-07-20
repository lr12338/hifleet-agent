# customer_ceshi Acceptance Status

Audit date: 2026-07-17. This is an evidence ledger, not a completion claim. `customer_support` remains outside the implementation scope.

| Requirement | Status | Evidence / limitation |
| --- | --- | --- |
| DeepSeek-led Responses tool loop | PASSED | Live Responses probe passed `function_call`, `function_call_output`, `call_id`, `previous_response_id`, and two tool rounds. |
| Doubao restricted to perception | PASSED | OSS image `/run` E2E records DeepSeek orchestrator and Doubao perception model; media calls return to the DeepSeek loop. |
| No tool-controlled completion | PASSED | Runtime no longer turns `can_answer`/recommended actions into `tool_choice=none`; focused regression coverage exists. |
| Deterministic coordinates/time | PASSED | Normalizers support degree-minute NSEW values and require confirmation for five-digit years. |
| Parser accuracy acceptance target | PARTIAL | `scripts/evaluate_customer_ceshi_parsers.py` reports 47/47 (100%) internally consistent, tool-evidenced position cases, 2/2 ambiguous-year safety cases, and 19/19 static-field coverage cases. This is a bounded evidence subset, not proof of the required corpus-wide ≥99% accuracy target. |
| Draft prepare/confirm/commit | PASSED | Durable, session-scoped Draft store; live `/run` prepare and bare `确认。` dry-run commit were exercised. |
| No false write success | PASSED | Accepted/dry-run commit is rendered as not production-written/not confirmed complete. |
| Scenario Contracts | PASSED | Tool-boundary contracts cover ship, update, platform, membership, and symbol scenarios. |
| Claim–Evidence Guard | PASSED | Unsupported high-risk sentence claims are removed; explicit write success remains a narrow exception. |
| Validated regression candidates | PASSED | `reports/customer_ceshi_eval/` separates P0/P1 manual review from validated candidates and excludes old replies as gold. |
| External `/run` compact text | PASSED | Live greeting request returned `customer_ceshi` response with one model call. |
| External `/stream_run` | PASSED | Live stream emitted customer_ceshi start/answer/end events with no customer_support debug leakage. |
| WeChat text compatibility | PASSED | Live legacy prompt request returned a customer_ceshi compact response. |
| OSS image `/run` | PASSED | All five non-reference image fixtures completed temporary upload → `/run` → cleanup against the latest runtime, each with DeepSeek orchestration and at least one Doubao perception call. |
| Image semantic accuracy acceptance target | FAILED | The conservative no-answer-persistence rubric in `reports/customer_ceshi_eval/image_semantic_rubric.json` most recently passed 1/5 fixtures on 2026-07-17; later live diagnostics showed the provider returning no usable visual facts for `image01`. Tool-call completion is therefore not treated as ≥95% semantic accuracy. |
| WeChat OSS image | PASSED | Live legacy image prompt completed with DeepSeek orchestration and Doubao perception. |
| Cross-session isolation | PASSED | Two live session IDs preserved separate compact responses. |
| Restart Draft recovery | PASSED | Store recreation test proves persistent local Draft recovery; worker restart used the configured store path. |
| customer_support regression | PASSED | Dedicated protected-chain suite: 226 passed on 2026-07-17 after the final `customer_ceshi` changes. |
| Deep thinking probe | PASSED | Direct DeepSeek Responses request with the documented `extra_body={"thinking":{"type":"enabled"}}` transport succeeded in the live probe. |
| Structured output probe | FAILED | Direct DeepSeek Responses JSON-schema request was not accepted/usable in the live probe. |
| Streaming probe | PASSED | Direct DeepSeek Responses stream emitted events in the live probe. |
| Audio OSS E2E | PASSED | A synthetic one-second silent WAV completed temporary OSS upload → `/run` → cleanup, with DeepSeek orchestration and one Doubao perception call on 2026-07-17. |
| Video OSS E2E | PASSED | A short public test MP4 completed temporary OSS upload → `/run` → cleanup, with DeepSeek orchestration and one Doubao perception call on 2026-07-17. |
| Context cache probe | FAILED | The direct `caching={"type":"enabled"}` live request was denied with `AccessDenied.CacheService`; no cache capability is claimed. |
| Context editing probe | PASSED | Direct DeepSeek request with documented `context_management.edits` succeeded; the runtime sends bounded thinking/tool-use edits. |
| Chat fallback live probe | PASSED | A deliberately unavailable Responses client fell back to a real DeepSeek Chat request inside `customer_ceshi`, with `runtime_mode=chat_function_calling` and no `customer_support` fallback. |
| Full P0/P1 scenario E2E | PASSED | Operational HTTP runner executed all 24 manual-review cases: 24 passed on 2026-07-17. No forbidden write tool executed. This is not semantic Gold evaluation. |
| Required observability | PASSED | Customer responses now expose requested/effective runtime, models, model/tool/media calls, cache hits, reasoning level, response-id tail, context turns, guard/fallback/finish results, output length, scenario, and Draft status without raw reasoning, keys, or signed URLs. |

## Completion Decision

**NOT_COMPLETE.** The goal cannot be declared complete while failed, partially passed, `NOT_IMPLEMENTED`, or `NOT_RUN` requirements remain. No `SKIPPED`, `NOT_RUN`, or `NOT_IMPLEMENTED` item is represented as passed.
