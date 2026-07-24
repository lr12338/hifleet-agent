from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from .auth import verify_admin_api_key
from .schemas import AdminTestRunRequest, ArkChatRequest, ChatDebugSessionListQuery, ChatDebugSessionSaveRequest, DashboardSummaryQuery, LLMConfigRequest, LogListQuery, SessionListQuery
from .service import (
    cancel_test_run,
    get_dashboard_summary,
    get_llm_config,
    get_log_detail,
    get_session_timeline,
    list_chat_debug_sessions,
    list_logs,
    list_sessions,
    proxy_test_run,
    remove_chat_debug_session,
    save_chat_debug_session,
    stream_ark_chat,
    stream_test_run,
    update_llm_config,
    upload_admin_file,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_api_key)])


@router.get("/logs")
async def admin_logs(
    start_time: str | None = Query(default=None),
    end_time: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    source_channel: str | None = Query(default=None),
    agent_profile: str | None = Query(default=None),
    route: str | None = Query(default=None),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
):
    query = LogListQuery(
        start_time=start_time,
        end_time=end_time,
        session_id=session_id,
        user_id=user_id,
        source_channel=source_channel,
        agent_profile=agent_profile,
        route=route,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return list_logs(query)


@router.get("/logs/{run_id}")
async def admin_log_detail(run_id: str):
    return get_log_detail(run_id)


@router.get("/dashboard/summary")
async def admin_dashboard_summary(
    start_time: str | None = Query(default=None),
    end_time: str | None = Query(default=None),
):
    query = DashboardSummaryQuery(start_time=start_time, end_time=end_time)
    return get_dashboard_summary(query)


@router.get("/sessions")
async def admin_sessions(
    start_time: str | None = Query(default=None),
    end_time: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    source_channel: str | None = Query(default=None),
    agent_profile: str | None = Query(default=None),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = SessionListQuery(
        start_time=start_time,
        end_time=end_time,
        user_id=user_id,
        source_channel=source_channel,
        agent_profile=agent_profile,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return list_sessions(query)


@router.get("/sessions/{session_id}")
async def admin_session_detail(session_id: str):
    return get_session_timeline(session_id)


@router.get("/config/llm")
async def admin_llm_config():
    return get_llm_config()


@router.put("/config/llm")
async def admin_llm_config_update(req: LLMConfigRequest):
    return update_llm_config(req)


@router.get("/chat-debug/sessions")
async def admin_chat_debug_sessions(limit: int = Query(default=20, ge=1, le=100)):
    query = ChatDebugSessionListQuery(limit=limit)
    return list_chat_debug_sessions(query.limit)


@router.put("/chat-debug/sessions/{session_key}")
async def admin_chat_debug_session_save(session_key: str, req: ChatDebugSessionSaveRequest):
    payload = req.model_copy(update={"session_key": session_key})
    return save_chat_debug_session(payload)


@router.delete("/chat-debug/sessions/{session_key}")
async def admin_chat_debug_session_delete(session_key: str):
    return remove_chat_debug_session(session_key)


@router.post("/test/run")
async def admin_test_run(req: AdminTestRunRequest, request: Request):
    is_streaming = req.stream or req.endpoint == "/stream_run"
    if not is_streaming:
        return await proxy_test_run(req)
    upstream_response, iterator = await stream_test_run(req, request)
    response = StreamingResponse(
        iterator,
        media_type=upstream_response.headers.get("content-type", "text/event-stream"),
        status_code=upstream_response.status_code,
    )
    run_id = req.run_id or ""
    if run_id:
        response.headers["x-run-id"] = run_id
    return response


@router.post("/test/cancel/{run_id}")
async def admin_test_cancel(run_id: str):
    return await cancel_test_run(run_id)


@router.post("/files/upload")
async def admin_file_upload(file: UploadFile = File(...)):
    return await upload_admin_file(file)


@router.post("/ark/chat")
async def admin_ark_chat(req: ArkChatRequest):
    iterator, request_body, resolved = await stream_ark_chat(req)
    response = StreamingResponse(iterator, media_type="text/event-stream")
    response.headers["x-admin-ark-model"] = str(resolved.get("model", ""))
    response.headers["x-admin-ark-thinking"] = str(resolved.get("thinking_type", ""))
    response.headers["x-admin-ark-modality"] = str(resolved.get("modality", "text"))
    response.headers["x-admin-request-size"] = str(len(str(request_body)))
    return response
