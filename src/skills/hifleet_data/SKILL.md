# HiFleet Data V2

You are using a locked, read-only data adapter for verified HiFleet vessel and
traffic data. State only facts that are directly supported by the returned data.
A successful HTTP/tool response alone never establishes that a customer-facing
conclusion is semantically correct; always include the tool result's version
metadata in trace data.

Do not expose account, billing, registration, purchase, contact-unlock, console,
charter-enrichment, or any other upstream write/review-required capability. Only
the approved read-only capabilities listed below are available; everything else
the upstream repository may contain must remain hidden.

## Conservative data rules

- Return vessel identity (ship name, MMSI/IMO), the queried data item, and its
  data time. When there is no result, state the query condition or data latency;
  never fabricate a record.
- Trajectory queries must respect the configured day limit; narrow the range
  instead of repeating an identical over-span request.
- Distinguish observed data, data latency, and unsupported product claims. Use
  hedged language ("可能/通常/不一定") only when evidence supports it.
- Never infer fields that the tool did not return, and never let a weak or
  conflicting web result override authoritative HiFleet data.

## Upstream provenance (single source of truth: skills-lock.json)

- upstream_repository: https://github.com/charleiWang/hifleet-skills
- version: 0.3.21
- commit: e4acf599192f3f1d247ef2da00e78d0cff89819c
- contentHash: 7118592bea375511a477bf29c7b882e01726fcc8f2e38df1c2bf60927c0e0f8a
- requiredEnv: HIFLEET_API_KEY
- verification: static-contract-reviewed

## Approved read-only upstream capabilities

get_archive, get_area_traffic, get_areas, get_avoidredsea_traffic, get_casualty,
get_maritime_penalty, get_port, get_position, get_psc, get_psc_anomalies,
get_psc_openclaw_stats, get_sanction, get_strait_traffic

## Review-required / rejected upstream capabilities (never auto-exposed)

charter_contact_dedup, charter_enrich_helpers, open_console

## Capability to adapter tool mapping

| adapter tool | upstream capability | description |
| --- | --- | --- |
| ship_search | (project adapter) | Search vessels by a user-supplied identifier. |
| get_ship_position | get_position | Read the latest vessel position. |
| get_ship_archive | get_archive | Read vessel archive data. |
| get_psc_records | get_psc | Read vessel PSC records. |
| get_area_traffic | get_area_traffic | Read area traffic statistics. |
| get_strait_traffic | get_strait_traffic | Read strait traffic statistics. |
| get_ship_trajectory | (project adapter) | Read vessel trajectory data. |
| get_ship_call_ports | (project adapter) | Read vessel port calls. |
| get_ship_voyages | (project adapter) | Read vessel voyages. |
| get_last_departure | (project adapter) | Read the last vessel departure. |
| get_current_stop | (project adapter) | Read the current vessel stop. |
| get_avoid_redsea_traffic | get_avoidredsea_traffic | Read red-sea avoidance traffic. |
| search_ports | get_port | Search ports. |
| get_port_detail | get_port | Read a port profile. |

"(project adapter)" marks HiFleet-API-backed tools that this project reviews and
exposes directly; they are not auto-derived from a new upstream script and any
new upstream capability remains review-required until explicitly mapped here.
