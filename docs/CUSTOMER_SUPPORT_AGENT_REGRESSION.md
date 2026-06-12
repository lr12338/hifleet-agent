# Customer Support Agent Regression

This document describes the production-oriented regression workflow for the
HiFleet `customer_support` routed agent.

## Scope

The regression validates the main production chain:

1. Message normalization and entity extraction.
2. Task classification.
3. Tool bundle shrinking.
4. Fast knowledge path and search fallback.
5. Single-step ship query.
6. Multi-step ship analysis with plan/act/check/fallback.
7. Ship statistics and area queries.
8. Write-operation gating and explicit real write test.

The runner is:

```bash
.venv/bin/python scripts/hifleet_agent_regression.py
```

The safe default mode performs real read API calls and a write validation case
that does not mutate ship data.

To run the explicit write regression against the configured test ship:

```bash
.venv/bin/python scripts/hifleet_agent_regression.py \
  --include-write \
  --write-lon 121.5 \
  --write-lat 31.2 \
  --write-speed 0 \
  --output artifacts/hifleet_agent_regression_report_with_write.json
```

Only run `--include-write` against a designated test MMSI.

## Test Vessels

- Query vessel: `yuming`
- Query MMSI: `414726000`
- Update test MMSI: `710001`

## Environment

The runner loads `/home/ecs-user/coze_ai/.env`.

Required key aliases:

- `HIFLEET_API_KEY`
- `hifleet_key1`
- `hifleet_key2`
- `api_key`
- `HIFLEET_TTSE_KEY`

The external skills repository uses `/home/ecs-user/skills/hifleet-skills/.env`.
The local `.env` should keep compatible aliases so both the local app and the
reference scripts can execute the same API families.

Do not print token values in logs or reports.

## Scenario Matrix

| ID | Purpose | Expected route | Expected tools |
|---|---|---|---|
| `knowledge_glossary_fast` | Platform glossary fast path | `knowledge` | `smart_search` |
| `ship_position_mmsi` | Direct MMSI position | `ship_single` | `get_ship_position` |
| `ship_position_name` | Bare ship-name resolution | `ship_single` | `ship_search`, `get_ship_position` |
| `ship_archive_mmsi` | Archive by MMSI | `ship_single` | `get_ship_archive` |
| `ship_complex_last_port_voyage` | Destination / recent call / voyage consistency | `ship_complex` | archive, position, call ports, last departure, voyages |
| `ship_complex_track_last_port` | Track history and last port by bare ship name | `ship_complex` | search, archive, position, trajectory, call ports, last departure |
| `strait_traffic_mandeb` | Strait traffic statistics | `ship_stats` | `get_strait_traffic` |
| `area_traffic_bbox` | Current vessels in bbox | `ship_stats` | `get_area_traffic` |
| `avoid_redsea_daily` | Red Sea diversion daily stats | `ship_stats` | `get_avoid_redsea_traffic` |
| `update_guard_missing_fields` | Write request with missing fields | `ship_update` | `upload_ship_position` validation |
| `update_position_real` | Explicit real write test | `ship_update` | `upload_ship_position` |

## Latest Regression Result

Safe read + guarded write validation:

- Command: `.venv/bin/python scripts/hifleet_agent_regression.py`
- Result: `10/10` passed
- Report: `artifacts/hifleet_agent_regression_report.json`

Explicit write regression:

- Command: `.venv/bin/python scripts/hifleet_agent_regression.py --include-write --write-lon 121.5 --write-lat 31.2 --write-speed 0 --output artifacts/hifleet_agent_regression_report_with_write.json`
- Result: `11/11` passed
- Report: `artifacts/hifleet_agent_regression_report_with_write.json`
- Mutation performed: updated test MMSI `710001` with lon `121.5`, lat `31.2`, speed `0`.

Observed latency in the latest successful run:

- Glossary fast path: about `4 ms`
- Direct ship position: about `290 ms`
- Ship-name position with search: about `504 ms`
- Ship archive: about `201 ms`
- Complex voyage analysis: about `4.9 s`
- Complex track/last-port analysis: about `1.5 s`
- Strait traffic: about `1.2 s`
- Area traffic bbox: about `607 ms`
- Red Sea diversion auth failure: about `217 ms`
- Real write update: about `367 ms`

## Fixes From Regression

The regression identified and fixed these issues:

- Bare ship names such as `yuming` were not resolved for `查询 yuming 船位`.
- Platform troubleshooting like `HiFleet 轨迹加载失败` could be misrouted to ship trajectory because of the word `轨迹`.
- `查询 yuming 近期轨迹，上一次停靠在哪个港口` was misrouted to port statistics because of the word `港口`.
- Red Sea diversion unauthorized responses were returned as raw JSON instead of a customer-safe authorization message.
- The complex ship harness did not surface ship-type inconsistencies between real-time position and archive data.
- The write chain did not parse update fields from natural language.

## Production Acceptance Criteria

The customer support chain is considered healthy when:

- Platform fast-path questions stay within `knowledge` and do not expose ship tools.
- Platform troubleshooting starts at `smart_search(depth="normal")`; quick KB can still be used for glossary/simple questions.
- Single-step ship queries use only the ship query bundle.
- Complex ship questions use the voyage bundle and produce a trace with entity resolution, tool sequence, loop count, check result, fallback reason, latency, and confidence.
- Write operations route only to `ship_update`; missing fields fail fast without mutation.
- Explicit writes require designated test MMSI and deliberate command-line opt-in.
- Unauthorized API families return clear authorization/fallback messages, not fabricated data.

## Known Remaining Risks

- `routerisk/getAvoidRedSeaDetail/token` currently returns authorization error with the configured token. The agent handles this safely, but the capability remains unavailable until the token scope is expanded.
- Port guide may also return authorization errors in this environment.
- Ship type can differ across APIs. The harness now reports this and treats archive as the stronger static source, but upstream data should be reconciled if this becomes user-facing noise.
- `pytest` is not installed in the current virtual environment; unit tests are currently executable through the lightweight direct runner used in this work.

