# Web Search V2

Use `web_search` only to perform a single, bounded public web search for candidate
evidence. This Skill exposes exactly one tool: `web_search`. It must never open,
click, navigate or otherwise operate a web page, and it must never include
`verify_public_page`, `agent_browser_deep_search` or `web_search_agent_browser`.

Every result must preserve its URL, title, snippet and source type. Weakly
related, conflicting or non-official sources are low-strength evidence only and
must not override authoritative HiFleet data or be presented as confirmed product
conclusions. Respect the per-turn call-count, timeout and duplicate-query budget;
narrow or rewrite the query instead of repeating an identical request.
