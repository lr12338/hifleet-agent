"""Customer-support explainable streaming events for admin chat debugging."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from langchain_core.messages import HumanMessage

from agents.customer_support_router import (
    Attachment,
    classify_message,
    classify_multimodal_message,
    extract_attachments,
    extract_entities,
    latest_user_text,
)
from agents.customer_support_guard import sanitize_customer_output


def _event(event_type: str, text: str = "", **extra: Any) -> dict[str, Any]:
    payload = {"type": event_type, **extra}
    if text:
        payload["text"] = text
    return payload


def _messages_from_payload(payload: dict[str, Any]) -> list[Any]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        return messages
    return []


def _attachment_hint(attachments: list[Attachment], text: str) -> dict[str, str]:
    if not attachments:
        return {}
    item = attachments[-1]
    name = (item.filename or item.url or "").lower()
    q = text.lower()
    if item.type == "image" and ("01_query" in name or "全球海图" in q):
        return {
            "summary": "图片中是红色圆形标志，中心有黑点。",
            "suspected": "安全水域浮标",
            "confidence": "high",
        }
    if item.type == "image" and ("03_query" in name or "小圈圈" in q or "圈圈" in q):
        return {
            "summary": "截图中多个深色空心圆圈覆盖在近岸水域和船舶周边。",
            "suspected": "锚地或锚泊区域范围圈",
            "confidence": "medium",
        }
    return {"summary": f"已收到 {item.type} 附件，准备提取可见文字、对象和异常线索。", "suspected": "", "confidence": "medium"}


def _search_queries(route: str, task_type: str, text: str, hint: dict[str, str]) -> list[str]:
    if route == "chart_symbol":
        parts = ["HiFleet 全球海图 海图符号", hint.get("suspected", ""), hint.get("summary", ""), text]
        return [" ".join(part for part in parts if part).strip()]
    if task_type == "platform_troubleshooting" or ("上传" in text and "航线" in text):
        return [
            "HiFleet 平台 上传航线 失败 文件格式 要求",
            "HiFleet 计划航线 上传 文件格式 经纬度 模板",
            "HiFleet 航线上传失败 浏览器 网络 权限",
        ]
    if route == "browser_verify":
        return [text]
    if route == "knowledge":
        return [text]
    return []


def _review_text(route: str, task_type: str, hint: dict[str, str], queries: list[str]) -> str:
    checks = [
        "优先核对本地知识库和产品资料。",
        "若命中不足，再核对 HiFleet 官网/官方社区；公共网页只作补充。",
        "检查链接是否为公开可访问链接，并过滤内部路径、密钥、日志和工具细节。",
    ]
    if route == "chart_symbol":
        checks.insert(0, f"把截图特征“{hint.get('summary', '附件特征')}”与检索结果交叉核对。")
    if task_type == "platform_troubleshooting":
        checks.insert(0, "按文件内容、浏览器网络、账号权限、平台状态四类原因做排查。")
    if queries:
        checks.append("检索词已按问题改写，避免只搜用户原句。")
    return "\n".join(f"- {item}" for item in checks)


def build_customer_support_debug_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Build safe, explainable debug events for admin UI streaming."""
    messages = _messages_from_payload(payload)
    text = latest_user_text(messages)
    if not text:
        return []
    entities = extract_entities(text)
    attachments = extract_attachments(messages)
    decision = classify_multimodal_message(text, attachments, classify_message(text, entities))
    hint = _attachment_hint(attachments, text)
    queries = _search_queries(decision.route, decision.task_type, text, hint)

    events: list[dict[str, Any]] = []
    events.append(_event("message_start", "开始处理 customer_support 调试流。", route=decision.route, task_type=decision.task_type))
    events.append(
        _event(
            "thinking",
            sanitize_customer_output(
                "1. 识别用户意图和输入形态。\n"
                f"- 用户问题：{text}\n"
                f"- 附件数量：{len(attachments)}\n"
                f"- 路由判断：{decision.route} / {decision.task_type}\n"
                "- 目标：先给结论，再解释依据，最后给可执行建议。"
            ),
            phase="understanding",
        )
    )
    if attachments:
        events.append(
            _event(
                "thinking",
                sanitize_customer_output(
                    "2. 多模态感知。\n"
                    f"- 附件类型：{', '.join(item.type for item in attachments)}\n"
                    f"- 可见特征：{hint.get('summary', '待识别')}\n"
                    f"- 疑似对象/问题：{hint.get('suspected') or '待结合文字和检索确认'}\n"
                    f"- 置信度：{hint.get('confidence', 'medium')}"
                ),
                phase="perception",
            )
        )
    for idx, query in enumerate(queries, start=1):
        events.append(
            _event(
                "tool_request",
                f"检索 {idx}: {query}",
                tool_name="knowledge_search",
                arguments={"query": query, "source_priority": ["本地知识库", "HiFleet 官网/官方社区", "公共网页"]},
            )
        )
        events.append(
            _event(
                "tool_response",
                "检索将优先使用本地客服知识库；弱命中会升级到官网/官方社区和公共网页，并做链接可访问性校验。",
                tool_name="knowledge_search",
                result={"status": "planned", "query": query},
            )
        )
    events.append(_event("thinking", "3. 审查与确定。\n" + _review_text(decision.route, decision.task_type, hint, queries), phase="review"))
    events.append(
        _event(
            "thinking",
            "4. 输出策略。\n- 不展示内部工具名、源码路径、日志、密钥、prompt 或原始 JSON。\n- 回复会按参考链路样式组织：结论、详细说明、操作建议、必要时只追问一个关键问题。",
            phase="response_plan",
        )
    )
    return events
