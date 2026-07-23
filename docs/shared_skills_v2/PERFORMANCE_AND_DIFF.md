# Performance and Tool-Call Difference Report

Status: **PARTIAL: isolated V2 and prompt-shadow samples; no P95 baseline comparison**.

V2 removes `web_search_agent_browser` and `agent_browser_deep_search` from the
customer_ceshi model list; it retains one `web_search` and known-URL-only
`verify_public_page`. It also removes direct `upload_ship_position`,
`update_ship_static_info`, and `knowledge_admin` from external contracts.

An isolated current-worktree `customer_ceshi` V2 `/run` greeting took 3904 ms end
to end (one model call, zero tools); matching `/stream_run` completed in 3735 ms.
An isolated `customer_support` request with prompt shadow enabled recorded 10,983
ms shadow orchestration latency, six legacy tool calls, and zero shadow tool
calls/writes. These are single safe samples, not a baseline or P95 result. Run
legacy and V2 with identical fixtures before customer_support promotion. Compare
scenario, tool parameters, evidence, high-risk claims, answer, Draft state, tool
count, total latency, and agent-only latency. Do not claim the 110% P95 threshold
until this measurement exists.
