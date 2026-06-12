"""Main agent assembly with employee_assistant execution loop."""
import json
import logging
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agents.profiles import (
    AgentProfile,
    PROFILE_HEADER,
    get_current_agent_profile_id,
    get_profile,
    read_profile_prompt,
)
from agents.customer_support_router import (
    BROWSER_FALLBACK_BUNDLE,
    KNOWLEDGE_BUNDLE,
    SHIP_QUERY_BUNDLE,
    SHIP_STATS_BUNDLE,
    SHIP_UPDATE_BUNDLE,
    SHIP_VOYAGE_BUNDLE,
    RouteDecision,
    classify_message,
    execute_complex_ship_chain,
    execute_knowledge_chain,
    execute_simple_ship_chain,
    execute_stats_chain,
    execute_update_chain,
    extract_entities,
    latest_user_text as latest_customer_user_text,
    make_trace,
)
from coze_coding_utils.runtime_ctx.context import default_headers
from llm_config import load_llm_config
from skills import SkillLoader
from storage.memory.memory_saver import get_memory_saver
from utils.llm_route_state import get_current_llm_route

LLM_CONFIG = "config/agent_llm_config.json"
SYSTEM_PROMPT_BASE = "config/system_prompt_base.md"
MAX_MESSAGES = 40
DEFAULT_SKILLS = {"hifleet_ship_service", "knowledge_qa"}
EMPLOYEE_MAX_LOOPS = int(os.getenv("HIFLEET_EMPLOYEE_MAX_LOOPS", "4"))
TABULAR_SUFFIXES = (".csv", ".xls", ".xlsx")

logger = logging.getLogger(__name__)


def _windowed_messages(old, new):
    merged = add_messages(old, new)
    cleaned = []
    for msg in merged:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None) and msg.content and isinstance(msg.content, str) and msg.content.strip():
            cleaned.append(
                AIMessage(
                    content="",
                    tool_calls=msg.tool_calls,
                    id=msg.id,
                    name=msg.name if hasattr(msg, "name") else None,
                    additional_kwargs=msg.additional_kwargs,
                )
            )
        else:
            cleaned.append(msg)

    latest_user_idx = -1
    for i in range(len(cleaned) - 1, -1, -1):
        if isinstance(cleaned[i], HumanMessage):
            latest_user_idx = i
            break

    def _sanitize_historical_content(content):
        if not isinstance(content, list):
            return content
        kept = []
        for seg in content:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type", "")).strip().lower()
            if seg_type in ("input_audio", "image_url", "video_url"):
                continue
            kept.append(seg)
        if not kept:
            return "（历史多媒体内容已省略，仅保留上下文结论）"
        if len(kept) == 1 and kept[0].get("type") == "text":
            return str(kept[0].get("text", "")).strip()
        return kept

    sanitized = []
    for idx, msg in enumerate(cleaned):
        if isinstance(msg, HumanMessage) and idx != latest_user_idx:
            new_content = _sanitize_historical_content(msg.content)
            if new_content != msg.content:
                try:
                    sanitized.append(msg.model_copy(update={"content": new_content}))
                except Exception:
                    msg.content = new_content
                    sanitized.append(msg)
            else:
                sanitized.append(msg)
        else:
            sanitized.append(msg)

    return sanitized[-MAX_MESSAGES:]


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], _windowed_messages]


class EmployeeAgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], _windowed_messages]
    session_id: str
    user_id: str
    source_channel: str
    agent_profile: str
    intent_hint: str
    status: str
    loop_count: int
    phase: Literal["route", "download", "plan", "act", "check", "loop", "done", "failed", "delegated"]
    phase_history: list[str]
    workspace_task: bool
    task_goal: str
    target_file_path: str
    source_file_url: str
    expected_artifact: str
    file_schema: dict[str, Any]
    generated_code: str
    sandbox_result: dict[str, Any]
    last_error: dict[str, Any]


class CustomerSupportState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], _windowed_messages]
    session_id: str
    user_id: str
    source_channel: str
    agent_profile: str
    intent_hint: str
    route: str
    task_type: str
    tool_bundle: list[str]
    entities: dict[str, Any]
    route_trace: dict[str, Any]
    fallback_reason: str


def _resolve_intent_hint(ctx=None, explicit_intent: str = "") -> str:
    if explicit_intent:
        return explicit_intent.strip().lower()
    if ctx is None:
        return ""
    headers = getattr(ctx, "headers", {}) or {}
    if isinstance(headers, dict):
        return str(headers.get("x-intent-hint", "")).strip().lower()
    return ""


def _resolve_agent_profile(ctx=None) -> AgentProfile:
    headers = getattr(ctx, "headers", {}) if ctx is not None else {}
    profile_id = ""
    if isinstance(headers, dict):
        profile_id = str(headers.get(PROFILE_HEADER, "")).strip()
    if not profile_id:
        profile_id = get_current_agent_profile_id()
    return get_profile(profile_id)


def classify_intent_fast(user_text: str, has_media: bool = False) -> str:
    text = (user_text or "").lower()
    if not text and has_media:
        return "knowledge"
    knowledge_priority_patterns = [
        "更新慢", "延迟", "异常", "报警", "告警", "为什么", "怎么", "怎么办", "无法",
        "失败", "收不到", "看不到", "不显示", "不刷新", "不准确", "功能", "教程",
        "使用", "说明", "帮助", "规则", "配置", "服务异常", "系统异常",
    ]
    if any(k in text for k in knowledge_priority_patterns):
        return "knowledge"

    ship_strong_patterns = [
        r"\bmmsi\b", r"\bimo\b", r"\b\d{9}\b", "查询船位", "更新船位", "上传船位",
        r"查.*船位", r"船位.*查", r"查.*位置", r"位置.*查",
        "船舶档案", "psc记录", "区域船舶", "海峡通航", "更新静态信息",
    ]
    for p in ship_strong_patterns:
        if re.search(p, text):
            return "ship"
    return "knowledge"


def _build_system_prompt(workspace_path: str, profile: AgentProfile, intent_hint: str = "") -> str:
    base_path = os.path.join(workspace_path, SYSTEM_PROMPT_BASE)
    with open(base_path, "r", encoding="utf-8") as f:
        parts = [f.read()]

    profile_prompt = read_profile_prompt(profile)
    if profile_prompt.strip():
        parts.append(f"\n\n---\n\n# Active Agent Profile: {profile.profile_id}\n\n{profile_prompt}")

    selected_skills = set(profile.skills or DEFAULT_SKILLS)
    skills_dir = os.path.join(workspace_path, "src/skills")
    if os.path.isdir(skills_dir):
        for skill_name in sorted(os.listdir(skills_dir)):
            if skill_name not in selected_skills:
                continue
            skill_path = os.path.join(skills_dir, skill_name)
            skill_md = os.path.join(skill_path, "SKILL.md")
            if os.path.isdir(skill_path) and os.path.exists(skill_md):
                with open(skill_md, "r", encoding="utf-8") as f:
                    skill_doc = f.read()
                parts.append(f"\n\n---\n\n# Skill: {skill_name}\n\n{skill_doc}")
                logger.info(f"[MainAgent] Loaded skill prompt: {skill_name} ({len(skill_doc)} chars)")

    full_prompt = "".join(parts)
    logger.info(
        f"[MainAgent] Total system prompt: {len(full_prompt)} chars, "
        f"profile={profile.profile_id}, intent_hint={intent_hint or 'none'}"
    )
    return full_prompt


def _load_all_tools(profile: AgentProfile) -> list:
    all_tools = SkillLoader.get_tools_by_skill_names(list(profile.skills or DEFAULT_SKILLS))
    disabled = set(profile.disabled_tools or [])
    if disabled:
        all_tools = [tool for tool in all_tools if tool.name not in disabled]
    logger.info(f"[MainAgent] Tools for profile={profile.profile_id}: {[t.name for t in all_tools]}")
    return all_tools


def _load_llm_config(workspace_path: str) -> dict[str, Any]:
    return load_llm_config(workspace_path)


def _resolve_runtime_llm_settings(ctx, cfg: dict[str, Any]) -> dict[str, str]:
    config = dict(cfg.get("config") or {})
    route = get_current_llm_route()
    requested_model = str(route.get("model", "")).strip()
    requested_thinking = str(route.get("thinking_type", "")).strip()
    model = requested_model or str(config.get("text_model") or config.get("model") or "doubao-seed-2-0-pro-260215").strip()
    thinking_type = requested_thinking or str(config.get("thinking_type") or "disabled").strip()
    return {"model": model, "thinking_type": thinking_type}


def _build_llm(ctx, cfg: dict[str, Any], *, streaming: bool) -> ChatOpenAI:
    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
    runtime_settings = _resolve_runtime_llm_settings(ctx, cfg)
    logger.info(
        "[MainAgent] Resolved model=%s thinking=%s streaming=%s",
        runtime_settings["model"],
        runtime_settings["thinking_type"],
        streaming,
    )
    return ChatOpenAI(
        model=runtime_settings["model"],
        api_key=api_key,
        base_url=base_url,
        temperature=cfg["config"].get("temperature", 0.7),
        streaming=streaming,
        timeout=cfg["config"].get("timeout", 600),
        extra_body={"thinking": {"type": runtime_settings["thinking_type"]}},
        default_headers=default_headers(ctx) if ctx else {},
    )


def _build_standard_agent(ctx, cfg: dict[str, Any], workspace_path: str, profile: AgentProfile, intent_hint: str = ""):
    logger.info("[MainAgent] Building standard agent graph")
    system_prompt = _build_system_prompt(workspace_path, profile=profile, intent_hint=intent_hint)
    llm = _build_llm(ctx, cfg, streaming=True)
    tools = _load_all_tools(profile)
    return create_agent(
        model=llm,
        system_prompt=system_prompt,
        tools=tools,
        checkpointer=get_memory_saver(),
        state_schema=AgentState,
    )


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _latest_user_text(messages: list[AnyMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return _content_to_text(msg.content)
        if isinstance(msg, dict) and str(msg.get("role", "")).lower() == "user":
            return _content_to_text(msg.get("content", ""))
    return ""


def _extract_local_file_path(text: str) -> str:
    candidates = re.findall(r"(?:[A-Za-z]:\\[^\\s'\"]+|/[^\\s'\"]+|[\\w./-]+)", text or "")
    for candidate in candidates:
        normalized = candidate.strip().strip('"').strip("'")
        lowered = normalized.lower()
        if lowered.startswith(("http://", "https://")):
            continue
        if lowered.endswith(TABULAR_SUFFIXES):
            return normalized
    return ""


def _extract_public_file_url(text: str) -> str:
    text = text or ""
    trailing_punct = ".,;!?，。；！？）】》」』、"
    delimiters = [" ", "\n", "\t", "\r", ")", "]", ">", '"', "'", "，", "。", "；", "！", "？", "）", "】", "》", "」", "』", "、"]
    for prefix in ("https://", "http://"):
        start_idx = text.find(prefix)
        if start_idx < 0:
            continue
        candidate = text[start_idx:]
        for delimiter in delimiters:
            candidate = candidate.split(delimiter, 1)[0]
        normalized = candidate.rstrip(trailing_punct)
        if normalized.lower().endswith(TABULAR_SUFFIXES):
            return normalized
    return ""


def _extract_expected_artifact(text: str, source_file: str) -> str:
    text_wo_urls = re.sub(r'https?://[^\s\)\]\>\"\']+', ' ', text or "")
    candidates = re.findall(r"[\w./-]+\.(?:xlsx|xls|csv)", text_wo_urls, flags=re.IGNORECASE)
    source_name = Path(source_file).name if source_file else ""
    for candidate in reversed(candidates):
        if Path(candidate).name != source_name:
            return candidate
    return ""


def _detect_workspace_task(profile: AgentProfile, messages: list[AnyMessage]) -> bool:
    if profile.profile_id != "employee_assistant":
        return False
    text = _latest_user_text(messages)
    if not text:
        return False
    has_tabular_input = bool(_extract_local_file_path(text) or _extract_public_file_url(text))
    if not has_tabular_input:
        return False
    keywords = ["分析", "表格", "csv", "excel", "xlsx", "报价", "统计", "数据", "生成", "python", "下载", "链接"]
    lowered = text.lower()
    return has_tabular_input and any(keyword.lower() in lowered for keyword in keywords)


def _extract_python_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _result_summary_message(state: EmployeeAgentState) -> str:
    result = state.get("sandbox_result") or {}
    artifacts = result.get("artifacts") or []
    stdout = str(result.get("stdout", "")).strip()
    lines = ["已完成受控数据任务。"]
    if artifacts:
        lines.append("产物：" + ", ".join(str(item) for item in artifacts[:5]))
    if stdout:
        lines.append("执行日志：\n" + stdout[-2000:])
    return "\n\n".join(lines)


def _failure_summary_message(state: EmployeeAgentState) -> str:
    last_error = state.get("last_error") or {}
    stderr = str(last_error.get("stderr", "")).strip()
    artifact_check = last_error.get("artifact_check") or {}
    lines = [f"自动修复已达到上限（{state.get('loop_count', 0)}/{EMPLOYEE_MAX_LOOPS}），任务未完成。"]
    if stderr:
        lines.append("最后一次错误：\n" + stderr[-2000:])
    elif artifact_check and not artifact_check.get("ok", True):
        lines.append("产物校验失败：" + json.dumps(artifact_check, ensure_ascii=False))
    return "\n\n".join(lines)


def _build_employee_agent(ctx, cfg: dict[str, Any], workspace_path: str, profile: AgentProfile, intent_hint: str = ""):
    standard_agent = _build_standard_agent(ctx, cfg, workspace_path, profile, intent_hint=intent_hint)
    codegen_llm = _build_llm(ctx, cfg, streaming=False)

    from skills.employee_workspace.tools import (
        download_public_file_to_artifact,
        inspect_tabular_file,
        run_sandboxed_python,
    )

    async def route_node(state: EmployeeAgentState) -> dict[str, Any]:
        user_text = _latest_user_text(state.get("messages", []))
        target_file_path = _extract_local_file_path(user_text)
        source_file_url = _extract_public_file_url(user_text)
        expected_artifact = _extract_expected_artifact(user_text, target_file_path or source_file_url)
        workspace_task = bool((target_file_path or source_file_url) and any(keyword in user_text for keyword in ["分析", "表格", "csv", "excel", "xlsx", "报价", "统计", "数据", "生成", "python", "下载", "链接"]))
        return {
            "phase": "route",
            "phase_history": ["route"],
            "workspace_task": workspace_task,
            "task_goal": user_text,
            "target_file_path": target_file_path,
            "source_file_url": source_file_url,
            "expected_artifact": expected_artifact,
            "loop_count": int(state.get("loop_count") or 0),
        }

    async def delegate_node(state: EmployeeAgentState) -> dict[str, Any]:
        payload = {
            "messages": state.get("messages", []),
            "session_id": state.get("session_id", ""),
            "user_id": state.get("user_id", ""),
            "source_channel": state.get("source_channel", ""),
            "agent_profile": state.get("agent_profile", profile.profile_id),
            "intent_hint": state.get("intent_hint", intent_hint),
        }
        delegated = await standard_agent.ainvoke(payload, context=ctx)
        delegated["phase"] = "delegated"
        delegated["status"] = delegated.get("status", "delegated")
        delegated["phase_history"] = list(state.get("phase_history", [])) + ["delegated"]
        delegated["workspace_task"] = False
        return delegated

    async def plan_node(state: EmployeeAgentState) -> dict[str, Any]:
        target_file_path = state.get("target_file_path") or _extract_local_file_path(state.get("task_goal", ""))
        source_file_url = state.get("source_file_url") or _extract_public_file_url(state.get("task_goal", ""))
        phase_history = list(state.get("phase_history", []))
        if not target_file_path and source_file_url:
            phase_history.append("download")
            download_raw = download_public_file_to_artifact.invoke({"file_url": source_file_url})
            download_payload = json.loads(download_raw)
            target_file_path = str(download_payload.get("local_path", "")).strip()
        raw = inspect_tabular_file.invoke({"file_path": target_file_path, "max_rows": 5})
        try:
            schema = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"inspect_tabular_file returned non-JSON payload: {raw}") from exc
        if schema.get("file") is None:
            raise RuntimeError(f"inspect_tabular_file failed: {raw}")
        phase_history.append("plan")
        return {"phase": "act", "phase_history": phase_history, "file_schema": schema, "target_file_path": target_file_path, "source_file_url": source_file_url}

    async def act_node(state: EmployeeAgentState) -> dict[str, Any]:
        prompt = f"""
你是 HiFleet employee_assistant 的受控 Python 执行器。
目标：{state.get('task_goal', '')}
原始文件：{state.get('target_file_path', '')}
原始链接：{state.get('source_file_url', '') or '无'}
期望产物：{state.get('expected_artifact', '') or '未指定'}
当前 loop 次数：{state.get('loop_count', 0)} / {EMPLOYEE_MAX_LOOPS}

文件 Schema（严禁臆造列名）：
{json.dumps(state.get('file_schema', {}), ensure_ascii=False, indent=2)}

上一轮失败信息：
{json.dumps(state.get('last_error', {}), ensure_ascii=False, indent=2)}

执行约束：
1. 只返回 Python 代码，不要解释。
2. 必须显式打印关键步骤与最终结果。
3. 代码必须只基于上面的 Schema 使用真实列名。
4. 输入文件必须通过 `Path(os.environ['INPUT_FILE'])` 读取，不要直接读取宿主机原始路径。
5. 生成文件时必须写入 `Path(os.environ['ARTIFACT_DIR'])` 目录。
6. 不要使用 eval/exec/compile/getattr/setattr，也不要访问任何双下划线属性。
"""
        response = await codegen_llm.ainvoke([
            SystemMessage(content="Return only executable Python code."),
            HumanMessage(content=prompt),
        ])
        code = _extract_python_code(_content_to_text(getattr(response, "content", response)))
        if not code:
            raise RuntimeError("LLM returned empty python code")
        return {"phase": "check", "phase_history": list(state.get("phase_history", [])) + ["act"], "generated_code": code}

    async def check_node(state: EmployeeAgentState) -> dict[str, Any]:
        attempt = int(state.get("loop_count") or 0) + 1
        raw = run_sandboxed_python.invoke(
            {
                "code": state.get("generated_code", ""),
                "expected_artifact": state.get("expected_artifact", ""),
                "attempt": attempt,
                "input_file_path": state.get("target_file_path", ""),
            }
        )
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"exit_code": 1, "stderr": raw, "artifact_check": {"ok": False, "reason": "non_json_tool_response"}}
        ok = result.get("exit_code") == 0 and (result.get("artifact_check") or {}).get("ok", True)
        phase_history = list(state.get("phase_history", [])) + ["check"]
        if ok:
            return {"phase": "done", "status": "success", "phase_history": phase_history, "sandbox_result": result}
        return {
            "phase": "loop",
            "phase_history": phase_history,
            "sandbox_result": result,
            "last_error": {
                "stderr": result.get("stderr", ""),
                "exit_code": result.get("exit_code"),
                "artifact_check": result.get("artifact_check", {}),
            },
        }

    async def loop_node(state: EmployeeAgentState) -> dict[str, Any]:
        return {
            "phase": "act",
            "phase_history": list(state.get("phase_history", [])) + ["loop"],
            "loop_count": int(state.get("loop_count") or 0) + 1,
        }

    async def finalize_node(state: EmployeeAgentState) -> dict[str, Any]:
        return {
            "phase": "done",
            "status": "success",
            "phase_history": list(state.get("phase_history", [])) + ["done"],
            "messages": [AIMessage(content=_result_summary_message(state))],
        }

    async def fail_node(state: EmployeeAgentState) -> dict[str, Any]:
        return {
            "phase": "failed",
            "status": "error",
            "phase_history": list(state.get("phase_history", [])) + ["failed"],
            "messages": [AIMessage(content=_failure_summary_message(state))],
        }

    def route_after_entry(state: EmployeeAgentState) -> str:
        if state.get("workspace_task"):
            return "plan"
        return "delegate"

    def route_after_check(state: EmployeeAgentState) -> str:
        if state.get("phase") == "done":
            return "finalize"
        if int(state.get("loop_count") or 0) >= EMPLOYEE_MAX_LOOPS:
            return "fail"
        return "loop"

    graph = StateGraph(EmployeeAgentState)
    graph.add_node("route", route_node)
    graph.add_node("delegate", delegate_node)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_node("check", check_node)
    graph.add_node("loop", loop_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("fail", fail_node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", route_after_entry, {"delegate": "delegate", "plan": "plan"})
    graph.add_edge("delegate", END)
    graph.add_edge("plan", "act")
    graph.add_edge("act", "check")
    graph.add_conditional_edges("check", route_after_check, {"finalize": "finalize", "loop": "loop", "fail": "fail"})
    graph.add_edge("loop", "act")
    graph.add_edge("finalize", END)
    graph.add_edge("fail", END)
    return graph.compile(checkpointer=get_memory_saver())


def _build_customer_support_agent(ctx, cfg: dict[str, Any], workspace_path: str, profile: AgentProfile, intent_hint: str = ""):
    logger.info("[MainAgent] Building customer_support routed graph")
    fallback_agent = _build_standard_agent(ctx, cfg, workspace_path, profile, intent_hint=intent_hint)
    allowed_write = bool((profile.tool_policy or {}).get("allow_write_actions", False))

    bundle_names = set(KNOWLEDGE_BUNDLE + SHIP_QUERY_BUNDLE + SHIP_STATS_BUNDLE + SHIP_VOYAGE_BUNDLE + BROWSER_FALLBACK_BUNDLE)
    if allowed_write:
        bundle_names.update(SHIP_UPDATE_BUNDLE)
    tools = SkillLoader.get_tools_by_names(sorted(bundle_names))
    tool_map = {tool.name: tool for tool in tools}

    async def route_node(state: CustomerSupportState) -> dict[str, Any]:
        text = latest_customer_user_text(state.get("messages", []))
        entities = extract_entities(text)
        decision = classify_message(text, entities)
        trace = make_trace(decision, entities, session_id=str(state.get("session_id", "")))
        logger.info(
            "[CustomerSupportRoute] run_id=%s session_id=%s route=%s task_type=%s bundle=%s entities=%s reason=%s",
            trace.run_id,
            trace.session_id,
            decision.route,
            decision.task_type,
            decision.tool_bundle,
            trace.entity_resolution,
            decision.reason,
        )
        return {
            "route": decision.route,
            "task_type": decision.task_type,
            "tool_bundle": decision.tool_bundle,
            "entities": trace.entity_resolution,
            "route_trace": asdict(trace),
        }

    async def execute_node(state: CustomerSupportState) -> dict[str, Any]:
        text = latest_customer_user_text(state.get("messages", []))
        entities = extract_entities(text)
        decision = classify_message(text, entities)
        trace = make_trace(decision, entities, session_id=str(state.get("session_id", "")))
        t0 = time.time()
        try:
            if decision.route == "knowledge":
                answer = execute_knowledge_chain(text, decision, tool_map, trace)
            elif decision.route == "ship_single":
                answer = execute_simple_ship_chain(text, decision, entities, tool_map, trace)
            elif decision.route == "ship_stats":
                answer = execute_stats_chain(text, entities, tool_map, trace)
            elif decision.route == "ship_complex":
                answer = execute_complex_ship_chain(text, entities, tool_map, trace, max_loops=2)
            elif decision.route == "ship_update":
                if not allowed_write:
                    trace.fallback_reason = "write action disabled by profile policy"
                    trace.check_result = {"write_allowed": False}
                    answer = "当前客服通道未开启船舶资料写操作。请联系人工客服处理：400-963-6899，微信客服：hifleetkhzs。"
                else:
                    answer = execute_update_chain(text, entities, tool_map, trace)
            else:
                trace.fallback_reason = "unsupported_route"
                delegated = await fallback_agent.ainvoke(state, context=ctx)
                delegated["route_trace"] = asdict(trace)
                return delegated

            trace.latency_hotspot["total"] = int((time.time() - t0) * 1000)
            logger.info(
                "[CustomerSupportTrace] run_id=%s session_id=%s route=%s task_type=%s sequence=%s loops=%s check=%s fallback=%s latency=%s confidence=%s",
                trace.run_id,
                trace.session_id,
                trace.route,
                trace.task_type,
                trace.tool_call_sequence,
                trace.loop_count,
                trace.check_result,
                trace.fallback_reason,
                trace.latency_hotspot,
                trace.answer_confidence,
            )
            return {
                "messages": [AIMessage(content=answer)],
                "route": trace.route,
                "task_type": trace.task_type,
                "tool_bundle": trace.tool_bundle,
                "entities": trace.entity_resolution,
                "route_trace": asdict(trace),
                "fallback_reason": trace.fallback_reason,
            }
        except Exception as exc:
            logger.exception("[CustomerSupportTrace] routed execution failed, fallback to standard agent: %s", exc)
            trace.fallback_reason = f"routed_execution_error:{type(exc).__name__}"
            delegated = await fallback_agent.ainvoke(state, context=ctx)
            delegated["route_trace"] = asdict(trace)
            delegated["fallback_reason"] = trace.fallback_reason
            return delegated

    graph = StateGraph(CustomerSupportState)
    graph.add_node("route", route_node)
    graph.add_node("execute", execute_node)
    graph.add_edge(START, "route")
    graph.add_edge("route", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=get_memory_saver())


def build_agent(ctx=None, intent: str = ""):
    logger.info("[MainAgent] Building Hifleet agent graph")
    workspace_path = os.getenv("COZE_WORKSPACE_PATH")
    if not workspace_path:
        workspace_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    cfg = _load_llm_config(workspace_path)
    intent_hint = _resolve_intent_hint(ctx, explicit_intent=intent)
    profile = _resolve_agent_profile(ctx)
    if profile.profile_id == "employee_assistant":
        agent = _build_employee_agent(ctx, cfg, workspace_path, profile, intent_hint=intent_hint)
        logger.info("[MainAgent] Employee loop graph built successfully")
        return agent
    if profile.profile_id == "customer_support":
        agent = _build_customer_support_agent(ctx, cfg, workspace_path, profile, intent_hint=intent_hint)
        logger.info("[MainAgent] Customer support routed graph built successfully")
        return agent
    agent = _build_standard_agent(ctx, cfg, workspace_path, profile, intent_hint=intent_hint)
    logger.info("[MainAgent] Standard agent built successfully")
    return agent
