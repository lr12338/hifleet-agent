# Performance and Tool-Call Difference Report

Status: **NOT_RUN (no configured live model/service baseline)**.

V2 removes `web_search_agent_browser` and `agent_browser_deep_search` from the
customer_ceshi model list; it retains one `web_search` and known-URL-only
`verify_public_page`. It also removes direct `upload_ship_position`,
`update_ship_static_info`, and `knowledge_admin` from external contracts.

Run baseline and V2 with identical fixtures before customer_support shadow
promotion. Compare scenario, tool parameters, evidence, high-risk claims, answer,
Draft state, tool count, total latency, and agent-only latency. Do not claim the
110% P95 threshold until this measurement exists.
