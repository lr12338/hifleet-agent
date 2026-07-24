# xfail 审计（customer_ceshi_v2）

`codex/shared-skills-v2` HEAD。`tests/customer_ceshi_v2/test_responses_runtime.py` 中 7 个
`xfail` 项已逐条审计。它们全部断言**已废弃的 Doubao 主导媒体行为**，这些行为在媒体编排改用
DeepSeek 主导循环（通过 `inspect_media`）时被有意移除。每项都有明确原因，不是真实失败的掩码；
除非重新引入已移除的架构，否则无法转为通过。

| 测试 | xfail 原因 | 审计结论 |
| --- | --- | --- |
| `test_single_model_router_uses_doubao_only_for_image_request` | 媒体须保持 DeepSeek 主导，不再选择 Doubao 业务循环 | 已废弃；保留。当前设计将媒体感知路由到 Doubao 但保持 DeepSeek 为编排器。 |
| `test_multimodal_responses_uses_doubao_read_only_tool_loop_and_previous_response_id` | Doubao 不再拥有业务工具或 previous_response_id 循环 | 已废弃；保留。Doubao 仅做感知。 |
| `test_multimodal_responses_supports_video_audio_and_mixed_content` | 混合媒体由 DeepSeek 通过 inspect_media 编排 | 已废弃；保留。 |
| `test_multimodal_responses_accepts_http_dict_messages` | HTTP 媒体消息不再调用独立 Doubao 运行时 | 已废弃；保留。 |
| `test_responses_stops_tools_after_answerable_search_result` | can_answer 元数据无法强制模型完成 | 已废弃；保留。循环有意不再在 `can_answer` 上强制停止。 |
| `test_media_ais_evidence_allows_one_follow_up_direct_position_update` | 媒体证据不能绕过 prepare/confirm/commit | 已废弃；保留。写入须经过 Draft 门禁。 |
| `test_media_ais_evidence_accepts_explicit_commands_and_confirm_only_once` | 直接媒体写入已被有意移除 | 已废弃；保留。 |

这些 `xfail` 标记后没有隐藏环境阻塞的失败。若媒体编排设计被重新审视，应重写这些测试以断言
新行为，而非逐字恢复。
