# xfail Audit (customer_ceshi_v2)

HEAD of `codex/shared-skills-v2`. The 7 `xfail` items in
`tests/customer_ceshi_v2/test_responses_runtime.py` were audited individually.
All assert **obsolete Doubao-led media behavior** that was intentionally removed
when media orchestration moved to a DeepSeek-led loop through `inspect_media`.
They carry specific reasons and are not masks for real failures; none could be
turned into a pass without re-introducing the removed architecture.

| Test | xfail reason | Audit verdict |
| --- | --- | --- |
| `test_single_model_router_uses_doubao_only_for_image_request` | media must remain DeepSeek-led and no longer selects a Doubao business loop | Obsolete; keep. Current design routes media perception to Doubao but keeps DeepSeek as orchestrator. |
| `test_multimodal_responses_uses_doubao_read_only_tool_loop_and_previous_response_id` | Doubao no longer owns business tools or previous_response_id loops | Obsolete; keep. Doubao is perception-only. |
| `test_multimodal_responses_supports_video_audio_and_mixed_content` | mixed media is orchestrated by DeepSeek through inspect_media | Obsolete; keep. |
| `test_multimodal_responses_accepts_http_dict_messages` | HTTP media messages no longer invoke a standalone Doubao runtime | Obsolete; keep. |
| `test_responses_stops_tools_after_answerable_search_result` | can_answer metadata cannot force model completion | Obsolete; keep. The loop deliberately no longer force-stops on `can_answer`. |
| `test_media_ais_evidence_allows_one_follow_up_direct_position_update` | media evidence cannot bypass prepare/confirm/commit | Obsolete; keep. Writes require the Draft gate. |
| `test_media_ais_evidence_accepts_explicit_commands_and_confirm_only_once` | direct media writes are intentionally removed | Obsolete; keep. |

No environment-blocked failure is hidden behind these `xfail` markers. If the
media orchestration design is revisited, these tests should be rewritten to
assert the new behavior rather than restored verbatim.
