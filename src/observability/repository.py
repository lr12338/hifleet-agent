from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from storage.database.db import get_engine

from .schemas import AgentErrorCreate, ApiCallCreate, LogListFilters, ToolInvocationCreate

_SCHEMA_READY = False


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


def _normalize_jsonb_payload(payload: dict[str, Any], json_fields: list[str]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in json_fields:
        if field in normalized and isinstance(normalized[field], (dict, list)):
            normalized[field] = json.dumps(normalized[field], ensure_ascii=False)
    return normalized


def _build_api_call_where(filters: LogListFilters, alias: str = "") -> tuple[str, dict[str, Any]]:
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters.start_time:
        clauses.append(f"{prefix}created_at >= :start_time")
        params["start_time"] = filters.start_time
    if filters.end_time:
        clauses.append(f"{prefix}created_at <= :end_time")
        params["end_time"] = filters.end_time
    if filters.session_id:
        clauses.append(f"{prefix}session_id = :session_id")
        params["session_id"] = filters.session_id
    if filters.user_id:
        clauses.append(f"{prefix}user_id = :user_id")
        params["user_id"] = filters.user_id
    if filters.source_channel:
        clauses.append(f"{prefix}source_channel = :source_channel")
        params["source_channel"] = filters.source_channel
    if filters.agent_profile:
        clauses.append(f"COALESCE({prefix}request_json->>'agent_profile', '') = :agent_profile")
        params["agent_profile"] = filters.agent_profile
    if filters.route:
        clauses.append(f"{prefix}route = :route")
        params["route"] = filters.route
    if filters.status:
        clauses.append(f"{prefix}status = :status")
        params["status"] = filters.status
    if filters.keyword:
        clauses.append(
            f"""(
                COALESCE({prefix}run_id, '') ILIKE :keyword
                OR COALESCE({prefix}session_id, '') ILIKE :keyword
                OR COALESCE({prefix}user_id, '') ILIKE :keyword
                OR COALESCE({prefix}route, '') ILIKE :keyword
                OR CAST(COALESCE({prefix}request_json, '{{}}'::jsonb) AS TEXT) ILIKE :keyword
                OR CAST(COALESCE({prefix}response_json, '{{}}'::jsonb) AS TEXT) ILIKE :keyword
            )"""
        )
        params["keyword"] = f"%{filters.keyword}%"

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def _append_where_clause(where_sql: str, clause: str) -> str:
    if not where_sql:
        return f"WHERE {clause}"
    return f"{where_sql} AND {clause}"


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    engine = get_engine()
    with engine.begin() as conn:
        sql_dir = Path(__file__).resolve().parent / "sql"
        for sql_file in sorted(sql_dir.glob("*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            statements = [stmt.strip() for stmt in sql.split(";") if stmt.strip()]
            for stmt in statements:
                conn.execute(text(stmt))
    _SCHEMA_READY = True


def insert_api_call(item: ApiCallCreate) -> None:
    ensure_schema()
    payload = _clean_payload(item.model_dump())
    payload = _normalize_jsonb_payload(payload, ["request_json", "response_json"])
    cols: list[str] = []
    binds: list[str] = []
    for key in payload.keys():
        cols.append(key)
        if key in ("request_json", "response_json"):
            binds.append(f"CAST(:{key} AS JSONB)")
        else:
            binds.append(f":{key}")
    sql = text(
        f"""
        INSERT INTO observability.api_calls ({", ".join(cols)})
        VALUES ({", ".join(binds)})
        ON CONFLICT (run_id) DO UPDATE SET
            session_id = EXCLUDED.session_id,
            user_id = EXCLUDED.user_id,
            source_channel = EXCLUDED.source_channel,
            route = EXCLUDED.route,
            intent_hint = EXCLUDED.intent_hint,
            request_json = EXCLUDED.request_json,
            response_json = EXCLUDED.response_json,
            http_status_code = EXCLUDED.http_status_code,
            status = EXCLUDED.status,
            latency_ms = EXCLUDED.latency_ms
        """
    )
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql, payload)


def insert_tool_invocation(item: ToolInvocationCreate) -> None:
    ensure_schema()
    payload = _clean_payload(item.model_dump())
    payload = _normalize_jsonb_payload(payload, ["tool_args", "tool_result", "layer_trace"])
    cols: list[str] = []
    binds: list[str] = []
    for key in payload.keys():
        cols.append(key)
        if key in ("tool_args", "tool_result", "layer_trace"):
            binds.append(f"CAST(:{key} AS JSONB)")
        else:
            binds.append(f":{key}")
    sql = text(
        f"INSERT INTO observability.tool_invocations ({', '.join(cols)}) VALUES ({', '.join(binds)})"
    )
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql, payload)


def insert_agent_error(item: AgentErrorCreate) -> None:
    ensure_schema()
    payload = _clean_payload(item.model_dump())
    cols = ", ".join(payload.keys())
    binds = ", ".join(f":{k}" for k in payload.keys())
    sql = text(f"INSERT INTO observability.agent_errors ({cols}) VALUES ({binds})")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql, payload)


def query_api_calls(filters: LogListFilters) -> dict[str, Any]:
    ensure_schema()
    where_sql, params = _build_api_call_where(filters)
    offset = (filters.page - 1) * filters.page_size
    params["limit"] = filters.page_size
    params["offset"] = offset

    list_sql = text(
        f"""
        SELECT id, run_id, session_id, user_id, source_channel,
               request_json ->> 'agent_profile' AS agent_profile,
               route, intent_hint, http_status_code, status, latency_ms, created_at
        FROM observability.api_calls
        {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(f"SELECT COUNT(*) AS total FROM observability.api_calls {where_sql}")
    stats_sql = text(
        f"""
        SELECT
            COUNT(*) AS request_count,
            COUNT(*) FILTER (WHERE status = 'error') AS failure_count,
            COUNT(*) FILTER (WHERE status = 'timeout') AS timeout_count,
            ROUND(COALESCE(AVG(latency_ms), 0))::int AS avg_latency_ms,
            COUNT(*) FILTER (WHERE route = '/stream_run') AS stream_count
        FROM observability.api_calls
        {where_sql}
        """
    )
    engine = get_engine()
    with engine.connect() as conn:
        items = [dict(row._mapping) for row in conn.execute(list_sql, params)]
        total = conn.execute(count_sql, params).scalar_one()
        stats = dict(conn.execute(stats_sql, params).mappings().first() or {})
    return {
        "total": int(total),
        "page": filters.page,
        "page_size": filters.page_size,
        "items": items,
        "stats": {
            "request_count": int(stats.get("request_count") or 0),
            "failure_count": int(stats.get("failure_count") or 0),
            "timeout_count": int(stats.get("timeout_count") or 0),
            "avg_latency_ms": int(stats.get("avg_latency_ms") or 0),
            "stream_ratio": round((int(stats.get("stream_count") or 0) / int(total)) * 100, 2) if int(total) else 0.0,
        },
    }


def query_log_detail(run_id: str) -> dict[str, Any]:
    ensure_schema()
    engine = get_engine()
    with engine.connect() as conn:
        api_call = conn.execute(
            text("SELECT * FROM observability.api_calls WHERE run_id = :run_id LIMIT 1"),
            {"run_id": run_id},
        ).mappings().first()
        tools = [
            dict(row._mapping)
            for row in conn.execute(
                text(
                    """
                    SELECT * FROM observability.tool_invocations
                    WHERE run_id = :run_id
                    ORDER BY created_at ASC
                    """
                ),
                {"run_id": run_id},
            )
        ]
        errors = [
            dict(row._mapping)
            for row in conn.execute(
                text(
                    """
                    SELECT * FROM observability.agent_errors
                    WHERE run_id = :run_id
                    ORDER BY created_at ASC
                    """
                ),
                {"run_id": run_id},
            )
        ]
    api_call_dict = dict(api_call) if api_call else None
    trace: list[dict[str, Any]] = []
    if api_call_dict:
        request_json = api_call_dict.get("request_json") or {}
        if isinstance(request_json, dict):
            api_call_dict["agent_profile"] = request_json.get("agent_profile")
        trace.append(
            {
                "type": "request",
                "created_at": api_call_dict.get("created_at"),
                "label": f"{api_call_dict.get('route')} · {api_call_dict.get('status')}",
                "payload": api_call_dict.get("request_json") or {},
            }
        )
    for tool in tools:
        attempt = int(tool.get("attempt") or 0)
        phase = ((tool.get("layer_trace") or {}).get("phase") if isinstance(tool.get("layer_trace"), dict) else None)
        label_parts = [tool.get("tool_name") or "tool"]
        if phase:
            label_parts.append(str(phase))
        if attempt:
            label_parts.append(f"attempt {attempt}")
        trace.append(
            {
                "type": "tool",
                "created_at": tool.get("created_at"),
                "label": " · ".join(label_parts),
                "payload": {
                    "request": tool.get("tool_args") or {},
                    "response": tool.get("tool_result") or {},
                    "status": tool.get("status"),
                    "message": tool.get("message"),
                    "attempt": attempt,
                    "layer_trace": tool.get("layer_trace") or {},
                },
            }
        )
    for error in errors:
        trace.append(
            {
                "type": "error",
                "created_at": error.get("created_at"),
                "label": error.get("error_code"),
                "payload": error,
            }
        )
    if api_call_dict:
        trace.append(
            {
                "type": "response",
                "created_at": api_call_dict.get("created_at"),
                "label": "response",
                "payload": api_call_dict.get("response_json") or {},
            }
        )
    return {
        "api_call": api_call_dict,
        "tool_invocations": tools,
        "errors": errors,
        "summary": {
            "run_id": run_id,
            "session_id": api_call_dict.get("session_id") if api_call_dict else None,
            "user_id": api_call_dict.get("user_id") if api_call_dict else None,
            "source_channel": api_call_dict.get("source_channel") if api_call_dict else None,
            "agent_profile": api_call_dict.get("agent_profile") if api_call_dict else None,
            "route": api_call_dict.get("route") if api_call_dict else None,
            "status": api_call_dict.get("status") if api_call_dict else None,
            "latency_ms": api_call_dict.get("latency_ms") if api_call_dict else None,
            "tool_count": len(tools),
            "error_count": len(errors),
        },
        "trace": trace,
    }


def query_session_calls(session_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    sql = text(
        """
        SELECT id, run_id, session_id, user_id, source_channel,
               request_json ->> 'agent_profile' AS agent_profile,
               route, intent_hint, http_status_code, status, latency_ms, created_at, request_json, response_json
        FROM observability.api_calls
        WHERE session_id = :session_id
        ORDER BY created_at ASC
        """
    )
    engine = get_engine()
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sql, {"session_id": session_id})]


def query_session_summaries(filters: LogListFilters) -> dict[str, Any]:
    ensure_schema()
    where_sql, params = _build_api_call_where(filters, alias="a")
    offset = (filters.page - 1) * filters.page_size
    params["limit"] = filters.page_size
    params["offset"] = offset
    session_where_sql = _append_where_clause(where_sql, "a.session_id IS NOT NULL")
    list_sql = text(
        f"""
        WITH filtered AS (
            SELECT *
            FROM observability.api_calls a
            {session_where_sql}
        ),
        agg AS (
            SELECT
                session_id,
                MIN(created_at) AS started_at,
                MAX(created_at) AS updated_at,
                COUNT(*) AS turn_count,
                COUNT(*) FILTER (WHERE status = 'error') AS error_count,
                ROUND(COALESCE(AVG(latency_ms), 0))::int AS avg_latency_ms
            FROM filtered
            GROUP BY session_id
        ),
        latest AS (
            SELECT DISTINCT ON (session_id)
                session_id,
                run_id AS latest_run_id,
                user_id,
                source_channel,
                request_json ->> 'agent_profile' AS agent_profile,
                route AS latest_route,
                status AS latest_status,
                request_json,
                response_json
            FROM filtered
            ORDER BY session_id, created_at DESC
        ),
        tools AS (
            SELECT
                f.session_id,
                COUNT(t.id) AS tool_count
            FROM filtered f
            LEFT JOIN observability.tool_invocations t ON t.run_id = f.run_id
            GROUP BY f.session_id
        )
        SELECT
            agg.session_id,
            agg.started_at,
            agg.updated_at,
            agg.turn_count,
            agg.error_count,
            agg.avg_latency_ms,
            latest.latest_run_id,
            latest.user_id,
            latest.source_channel,
            latest.agent_profile,
            latest.latest_route,
            latest.latest_status,
            latest.request_json,
            latest.response_json,
            COALESCE(tools.tool_count, 0) AS tool_count
        FROM agg
        JOIN latest ON latest.session_id = agg.session_id
        LEFT JOIN tools ON tools.session_id = agg.session_id
        ORDER BY agg.updated_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(
        f"""
        WITH filtered AS (
            SELECT session_id
            FROM observability.api_calls a
            {session_where_sql}
        )
        SELECT COUNT(DISTINCT session_id) AS total FROM filtered
        """
    )
    engine = get_engine()
    with engine.connect() as conn:
        items = [dict(row._mapping) for row in conn.execute(list_sql, params)]
        total = conn.execute(count_sql, params).scalar_one()
    return {"items": items, "total": int(total), "page": filters.page, "page_size": filters.page_size}


def query_dashboard_summary(filters: LogListFilters) -> dict[str, Any]:
    ensure_schema()
    where_sql, params = _build_api_call_where(filters, alias="a")
    engine = get_engine()
    kpi_sql = text(
        f"""
        WITH filtered AS (
            SELECT *
            FROM observability.api_calls a
            {where_sql}
        ),
        session_stats AS (
            SELECT COUNT(DISTINCT session_id) AS session_count
            FROM filtered
            WHERE session_id IS NOT NULL
        ),
        tool_stats AS (
            SELECT
                COUNT(t.id) AS tool_total,
                COUNT(t.id) FILTER (WHERE t.status IN ('success', 'ok')) AS tool_success
            FROM filtered f
            LEFT JOIN observability.tool_invocations t ON t.run_id = f.run_id
        )
        SELECT
            COUNT(*) AS request_count,
            COUNT(*) FILTER (
                WHERE status NOT IN ('error', 'timeout', 'cancelled', 'bad_request', 'bad_json')
            ) AS success_count,
            COUNT(*) FILTER (WHERE status = 'error') AS error_count,
            ROUND(COALESCE(AVG(latency_ms), 0))::int AS avg_latency_ms,
            COALESCE((SELECT session_count FROM session_stats), 0) AS session_count,
            COALESCE((SELECT tool_total FROM tool_stats), 0) AS tool_total,
            COALESCE((SELECT tool_success FROM tool_stats), 0) AS tool_success
        FROM filtered
        """
    )
    trend_sql = text(
        f"""
        SELECT
            DATE_TRUNC('hour', a.created_at) AS bucket,
            COUNT(*) AS requests,
            COUNT(*) FILTER (WHERE a.status = 'error') AS errors,
            ROUND(COALESCE(AVG(a.latency_ms), 0))::int AS avg_latency_ms
        FROM observability.api_calls a
        {where_sql}
        GROUP BY 1
        ORDER BY 1 ASC
        LIMIT 48
        """
    )
    channel_sql = text(
        f"""
        SELECT COALESCE(a.source_channel, 'unknown') AS label, COUNT(*) AS value
        FROM observability.api_calls a
        {where_sql}
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 5
        """
    )
    route_sql = text(
        f"""
        SELECT COALESCE(a.route, 'unknown') AS label, COUNT(*) AS value
        FROM observability.api_calls a
        {where_sql}
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 5
        """
    )
    profile_sql = text(
        f"""
        SELECT COALESCE(a.request_json ->> 'agent_profile', 'unknown') AS label, COUNT(*) AS value
        FROM observability.api_calls a
        {where_sql}
        GROUP BY 1
        ORDER BY value DESC
        LIMIT 5
        """
    )
    risky_sql = text(
        f"""
        SELECT
            a.session_id,
            MAX(a.user_id) AS user_id,
            MAX(a.source_channel) AS source_channel,
            MAX(a.request_json ->> 'agent_profile') AS agent_profile,
            MAX(a.created_at) AS updated_at,
            COUNT(*) AS turn_count,
            COUNT(*) FILTER (WHERE a.status = 'error') AS error_count,
            ROUND(COALESCE(AVG(a.latency_ms), 0))::int AS avg_latency_ms
        FROM observability.api_calls a
        {_append_where_clause(where_sql, 'a.session_id IS NOT NULL')}
        GROUP BY a.session_id
        ORDER BY error_count DESC, avg_latency_ms DESC, updated_at DESC
        LIMIT 5
        """
    )
    with engine.connect() as conn:
        kpi = dict(conn.execute(kpi_sql, params).mappings().first() or {})
        trends = [dict(row._mapping) for row in conn.execute(trend_sql, params)]
        by_channel = [dict(row._mapping) for row in conn.execute(channel_sql, params)]
        by_route = [dict(row._mapping) for row in conn.execute(route_sql, params)]
        by_profile = [dict(row._mapping) for row in conn.execute(profile_sql, params)]
        risky_sessions = [dict(row._mapping) for row in conn.execute(risky_sql, params)]
    request_count = int(kpi.get("request_count") or 0)
    success_count = int(kpi.get("success_count") or 0)
    tool_total = int(kpi.get("tool_total") or 0)
    tool_success = int(kpi.get("tool_success") or 0)
    return {
        "kpis": {
            "request_count": request_count,
            "session_count": int(kpi.get("session_count") or 0),
            "success_rate": round((success_count / request_count) * 100, 2) if request_count else 0.0,
            "avg_latency_ms": int(kpi.get("avg_latency_ms") or 0),
            "error_count": int(kpi.get("error_count") or 0),
            "tool_success_rate": round((tool_success / tool_total) * 100, 2) if tool_total else 0.0,
            "estimated_cost": round(request_count * 0.002, 2),
        },
        "trends": trends,
        "distribution": {
            "by_channel": by_channel,
            "by_route": by_route,
            "by_profile": by_profile,
        },
        "health": {
            "service": "healthy",
            "model": "connected",
            "dependencies": "operational",
            "version": "admin-ui-v2",
        },
        "risky_sessions": risky_sessions,
    }


def upsert_chat_debug_session(
    *,
    session_key: str,
    title: str,
    status: str,
    meta_session_id: str,
    user_id: str,
    source_channel: str,
    model: str,
    payload: dict[str, Any],
) -> None:
    ensure_schema()
    sql = text(
        """
        INSERT INTO observability.chat_debug_sessions (
            session_key, title, status, meta_session_id, user_id, source_channel, model, payload
        ) VALUES (
            :session_key, :title, :status, :meta_session_id, :user_id, :source_channel, :model, CAST(:payload AS JSONB)
        )
        ON CONFLICT (session_key) DO UPDATE SET
            title = EXCLUDED.title,
            status = EXCLUDED.status,
            meta_session_id = EXCLUDED.meta_session_id,
            user_id = EXCLUDED.user_id,
            source_channel = EXCLUDED.source_channel,
            model = EXCLUDED.model,
            payload = EXCLUDED.payload,
            updated_at = NOW()
        """
    )
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            sql,
            {
                "session_key": session_key,
                "title": title,
                "status": status,
                "meta_session_id": meta_session_id,
                "user_id": user_id,
                "source_channel": source_channel,
                "model": model,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )


def query_chat_debug_sessions(limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema()
    sql = text(
        """
        SELECT session_key, title, status, meta_session_id, user_id, source_channel, model, payload, created_at, updated_at
        FROM observability.chat_debug_sessions
        ORDER BY updated_at DESC, created_at DESC
        LIMIT :limit
        """
    )
    engine = get_engine()
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sql, {"limit": limit})]


def delete_chat_debug_session(session_key: str) -> None:
    ensure_schema()
    sql = text("DELETE FROM observability.chat_debug_sessions WHERE session_key = :session_key")
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sql, {"session_key": session_key})
