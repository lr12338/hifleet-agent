"""Controlled local knowledge-base maintenance tools."""
from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

from langchain.tools import tool

from agents.profiles import get_current_agent_profile_id, load_profiles_config
from coze_coding_utils.log.write_log import request_context
from skills.knowledge_qa.local_kb_runtime import (
    build_local_kb_index,
    normalize_query_text,
    reset_local_kb_index,
    search_local_kb_structured,
    workspace_root,
)
from utils.context_headers import get_context_headers

KB_UPDATE_HEADER = "x-kb-update-key"
KB_UPDATE_ENV = "HIFLEET_KB_UPDATE_KEY"
DEFAULT_CATEGORY = "平台操作"


def _kb_jsonl_path() -> Path:
    override = os.getenv("HIFLEET_KB_JSONL_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return workspace_root() / "docs" / "RAG" / "hifleet_cs_outputs" / "客服知识库结构化.jsonl"


def _canonicalize_profile(profile_id: str) -> str:
    config = load_profiles_config()
    profiles = config.get("profiles", {}) or {}
    normalized = (profile_id or "").strip()
    if normalized in profiles:
        return normalized
    for canonical_id, data in profiles.items():
        aliases = data.get("aliases", []) if isinstance(data, dict) else []
        if normalized in {str(alias).strip() for alias in aliases if str(alias).strip()}:
            return canonical_id
    return normalized


def _request_headers() -> dict[str, Any]:
    ctx = request_context.get()
    headers = getattr(ctx, "headers", None) if ctx else None
    if isinstance(headers, dict):
        return headers
    if ctx is not None:
        return get_context_headers(ctx)
    return {}


def _current_profile() -> str:
    profile = _canonicalize_profile(get_current_agent_profile_id())
    if profile:
        return profile
    headers = _request_headers()
    for key in ("x-agent-profile", "X-Agent-Profile"):
        if headers.get(key):
            return _canonicalize_profile(str(headers.get(key)))
    return ""


def _extract_text_key(raw_text: str) -> str:
    patterns = [
        r"(?:kb[_-]?key|key|授权key|知识库key)\s*[:：=]\s*([A-Za-z0-9._\-]{6,128})",
        r"\[kb[_-]?key\s*[:：=]\s*([A-Za-z0-9._\-]{6,128})\]",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _strip_text_key(value: str) -> str:
    return re.sub(
        r"(?:kb[_-]?key|key|授权key|知识库key)\s*[:：=]\s*[A-Za-z0-9._\-]{6,128}",
        "",
        str(value or ""),
        flags=re.IGNORECASE,
    )


def _authorized(raw_text: str) -> tuple[bool, str]:
    expected = os.getenv(KB_UPDATE_ENV, "").strip()
    if not expected:
        return False, "kb_update_key_not_configured"
    headers = _request_headers()
    for key, value in headers.items():
        if str(key).casefold() == KB_UPDATE_HEADER and str(value or "").strip():
            return False, "header_key_not_supported"
    supplied = _extract_text_key(raw_text)
    if supplied != expected:
        return False, "invalid_or_missing_kb_update_key"
    profile = _current_profile()
    if profile not in {"customer_support", "customer_ceshi"}:
        return False, "profile_not_allowed"
    return True, ""


def _strip_update_prefix(text: str) -> str:
    value = str(text or "").strip()
    value = _strip_text_key(value)
    value = re.sub(
        r"^\s*(?:添加|纠正|更新)知识库\s*[：:，,]?\s*",
        "",
        value,
        count=1,
    ).strip()
    return value.lstrip("：:，, \t\r\n").strip()


def _has_update_command(raw_text: str) -> bool:
    return bool(re.search(r"(?:添加|纠正|更新)知识库(?:\s*[：:，,]|\s|$)", raw_text or ""))


def _extract_sources(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)）\]】>\"']+", text or "")
    deduped: list[str] = []
    for url in urls:
        clean = url.rstrip(".,;!?，。；！？）】》")
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped[:5]


def _split_question_answer(content: str, question: str = "", answer: str = "") -> tuple[str, str]:
    q = str(question or "").strip()
    a = str(answer or "").strip()
    content = _strip_update_prefix(content)
    if q and a:
        return q, a

    question_patterns = [
        r"(?:适用问题|问题|用户问题|query|question)\s*[:：]\s*(.+?)(?:\n|$)",
        r"(在HiFleet上[^。\n]{2,80}\?)",
        r"(怎么[^。\n]{2,80})",
        r"(如何[^。\n]{2,80})",
    ]
    if not q:
        for pattern in question_patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                q = match.group(1).strip(" ：:。")
                break
    if not q:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        if "：" in first_line or ":" in first_line:
            separator = "：" if "：" in first_line else ":"
            topic, detail = [part.strip(" ：:。") for part in first_line.split(separator, 1)]
            if any(marker in first_line for marker in ("图标", "标识", "符号", "泊位", "颜色")) and detail:
                q = _build_short_fact_question(topic, detail)
            else:
                q = topic[:120]
        elif len(first_line) <= 80:
            q = first_line.strip(" ：:。")

    if not a:
        answer_markers = ["标准答案：", "标准答案:", "正确信息：", "正确信息:", "答案：", "答案:"]
        for marker in answer_markers:
            if marker in content:
                a = content.split(marker, 1)[1].strip()
                break
    if not a:
        a = content
        if q and a.startswith(q):
            a = a[len(q):].strip(" ：:\n")
    a = _normalize_short_fact_answer(q, a)

    return q[:120], a.strip()


def _build_short_fact_question(topic: str, detail: str) -> str:
    text = f"{topic} {detail}"
    if "紫色点圈" in text and "灰绿色点" in text:
        return "HiFleet 海图中紫色点圈、中心有灰绿色点是什么图标？"
    if "图标" in text or "标识" in text or "符号" in text:
        cleaned_topic = topic or "HiFleet 海图标识"
        return f"{cleaned_topic}是什么？"[:120]
    return (topic or text)[:120]


def _normalize_short_fact_answer(question: str, answer: str) -> str:
    value = str(answer or "").strip()
    text = f"{question}\n{value}"
    if "紫色点圈" in text and "灰绿色点" in text and "泊位" in text:
        return "HiFleet 海图中紫色点圈且中心有灰绿色点，可识别为泊位图标。"
    return value


def _preferred_keywords(question: str, answer: str) -> list[str]:
    text = f"{question}\n{answer}"
    if "紫色点圈" in text and "灰绿色点" in text and "泊位" in text:
        return ["海图标识", "泊位图标", "紫色点圈", "灰绿色点", "图标识别", "HiFleet海图"]
    return []


def _token_keywords(text: str) -> list[str]:
    candidates = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,40}|[\u4e00-\u9fff]{2,12}", text or "")
    stopwords = {"添加知识库", "纠正知识库", "更新知识库", "标准答案", "正确信息", "完整操作步骤", "用户输入", "参考", "详情链接"}
    deduped: list[str] = []
    for item in candidates:
        cleaned = item.strip(" ：:。,.，")
        if not cleaned or cleaned in stopwords:
            continue
        if cleaned not in deduped:
            deduped.append(cleaned)
        if len(deduped) >= 12:
            break
    return deduped


def _normalize_keywords(keywords: Any, content: str) -> list[str]:
    if isinstance(keywords, str):
        raw = re.split(r"[,，、\s]+", keywords)
    elif isinstance(keywords, list):
        raw = [str(item) for item in keywords]
    else:
        raw = []
    deduped: list[str] = []
    for item in raw:
        cleaned = item.strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    for item in _token_keywords(content):
        if item not in deduped:
            deduped.append(item)
        if len(deduped) >= 12:
            break
    return deduped[:12]


def _infer_category_intent(question: str, answer: str, category: str = "", intent: str = "") -> tuple[str, str]:
    text = f"{question}\n{answer}"
    resolved_category = category.strip() or DEFAULT_CATEGORY
    resolved_intent = intent.strip()
    if not resolved_intent:
        if any(marker in text for marker in ("图标", "符号", "颜色", "紫色", "泊位")):
            resolved_category = category.strip() or "海图图标"
            resolved_intent = "chart_symbol_knowledge"
        elif any(marker in text for marker in ("保存不了", "无法", "失败", "不显示", "不触发", "找不到")):
            resolved_category = category.strip() or "常见问题"
            resolved_intent = "platform_troubleshooting"
        elif any(marker in text for marker in ("怎么", "如何", "步骤", "绘制", "添加", "设置", "操作")):
            resolved_intent = "platform_operation"
        else:
            resolved_intent = "platform_knowledge"
    return resolved_category, resolved_intent


def _looks_like_chart_symbol_content(text: str) -> bool:
    markers = ("海图", "图标", "标识", "图标名称", "图标描述", "OCR", "S52")
    return any(marker in text for marker in markers)


def _parse_mapping_rows(raw_text: str) -> list[tuple[str, str]]:
    content = _strip_update_prefix(raw_text)
    rows: list[tuple[str, str]] = []
    skip_names = {"图标名称", "名称", "问题", "答案", "key", "授权key", "知识库key"}
    for line in content.splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value.startswith("|"):
            continue
        if set(value) <= {"-", "—", "=", " "}:
            continue
        value = re.sub(r"^\s*[-*+]\s*", "", value)
        if "：" in value:
            name, desc = value.split("：", 1)
        elif ":" in value:
            name, desc = value.split(":", 1)
        else:
            continue
        name = name.strip(" ：:。`*")
        desc = desc.strip(" ：:。")
        if not name or name in skip_names or len(name) > 40 or len(desc) < 6:
            continue
        if re.search(r"(?:kb[_-]?key|授权key|知识库key|^key$)", name, flags=re.IGNORECASE):
            continue
        rows.append((name, desc))
    return rows


def _mapping_entry(name: str, desc: str, kb_id: str) -> dict[str, Any]:
    keywords = [name, "海图标识", "图标识别", "HiFleet海图"]
    for item in _token_keywords(f"{name} {desc}"):
        if item not in keywords:
            keywords.append(item)
        if len(keywords) >= 10:
            break
    return {
        "id": kb_id,
        "category": "海图图标",
        "intent": "chart_symbol_knowledge",
        "question": f"HiFleet 海图中“{name}”是什么图标/有什么识别特征？",
        "answer": f"HiFleet 海图中，{name}的识别特征是：{desc}。",
        "keywords": keywords[:10],
        "related_topics": [],
        "sources": [],
        "escalate_when": ["用户提供截图需要进一步识别", "页面实际图标与知识库描述不一致"],
    }


def _mapping_duplicate_match(name: str, entry: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_name = normalize_query_text(name)
    normalized_question = normalize_query_text(str(entry.get("question", "")))
    for item in items:
        existing_question = str(item.get("question", ""))
        if normalize_query_text(existing_question) == normalized_question:
            return {"duplicate": True, "reason": "same_question", "id": item.get("id", ""), "question": existing_question}
        existing_keywords = {
            normalize_query_text(str(v))
            for v in list(item.get("keywords") or [])
            if str(v).strip()
        }
        if normalized_name in existing_keywords:
            return {"duplicate": True, "reason": "same_symbol_name", "id": item.get("id", ""), "question": existing_question}
    return {"duplicate": False}


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _validate_jsonl(path)
    reset_local_kb_index()
    build_local_kb_index()


def _upsert_mapping_entries(raw_text: str, existing_items: list[dict[str, Any]], path: Path) -> dict[str, Any] | None:
    clean_raw_text = _strip_text_key(raw_text)
    if not _looks_like_chart_symbol_content(clean_raw_text):
        return None
    rows = _parse_mapping_rows(raw_text)
    if len(rows) < 2:
        return None

    next_num = 0
    for item in existing_items:
        match = re.fullmatch(r"kb_(\d+)", str(item.get("id", "")).strip())
        if match:
            next_num = max(next_num, int(match.group(1)))

    inserted: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    failed_rows = 0
    all_items = list(existing_items)
    for name, desc in rows:
        next_num += 1
        entry = _mapping_entry(name, desc, f"kb_{next_num:03d}")
        duplicate = _mapping_duplicate_match(name, entry, all_items)
        if duplicate.get("duplicate"):
            duplicates.append({"name": name, "id": duplicate.get("id", ""), "reason": duplicate.get("reason", "")})
            next_num -= 1
            continue
        if len(entry["answer"]) < 20:
            failed_rows += 1
            next_num -= 1
            continue
        inserted.append(entry)
        all_items.append(entry)

    if inserted:
        _write_entries(path, inserted)
        _audit("batch_upserted", {"question": inserted[0]["question"], "duplicate": False, "id": inserted[0]["id"]})

    return {
        "ok": bool(inserted),
        "status": "batch_upserted" if inserted and not duplicates and failed_rows == 0 else "partial",
        "inserted_count": len(inserted),
        "duplicate_count": len(duplicates),
        "failed_count": failed_rows,
        "skipped_unparsed_count": max(0, len(clean_raw_text.splitlines()) - len(rows)),
        "sample_ids": [item["id"] for item in inserted[:5]],
        "duplicate_samples": duplicates[:5],
        "recommended_test_query": inserted[0]["question"] if inserted else "",
    }


def _load_jsonl_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            items.append(value)
    return items


def _next_kb_id(items: list[dict[str, Any]]) -> str:
    max_id = 0
    for item in items:
        match = re.fullmatch(r"kb_(\d+)", str(item.get("id", "")).strip())
        if match:
            max_id = max(max_id, int(match.group(1)))
    return f"kb_{max_id + 1:03d}"


def _similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, normalize_query_text(left), normalize_query_text(right)).ratio()


def _duplicate_match(question: str, answer: str, keywords: list[str], items: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_question = normalize_query_text(question)
    keyword_set = {normalize_query_text(item) for item in keywords if item}
    for item in items:
        existing_question = str(item.get("question", ""))
        if normalize_query_text(existing_question) == normalized_question:
            return {"duplicate": True, "reason": "same_question", "id": item.get("id", ""), "question": existing_question}
        existing_keywords = {normalize_query_text(str(v)) for v in list(item.get("keywords") or []) if str(v).strip()}
        overlap = len(keyword_set & existing_keywords)
        answer_similarity = _similarity(answer, str(item.get("answer", "")))
        question_similarity = _similarity(question, existing_question)
        if (overlap >= 3 and answer_similarity >= 0.55) or question_similarity >= 0.88:
            return {
                "duplicate": True,
                "reason": "similar_entry",
                "id": item.get("id", ""),
                "question": existing_question,
                "keyword_overlap": overlap,
                "answer_similarity": round(answer_similarity, 4),
                "question_similarity": round(question_similarity, 4),
            }
    return {"duplicate": False}


def _validate_jsonl(path: Path) -> None:
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            json.loads(line)


def _audit(status: str, payload: dict[str, Any]) -> None:
    ctx = request_context.get()
    logger_payload = {
        "status": status,
        "run_id": getattr(ctx, "run_id", "") if ctx else "",
        "session_user": getattr(ctx, "user_id", "") if ctx else "",
        "profile": _current_profile(),
        "kb_id": payload.get("id", ""),
        "question": payload.get("question", ""),
        "duplicate": payload.get("duplicate", False),
    }
    import logging

    logging.getLogger(__name__).info("[knowledge_admin] %s", json.dumps(logger_payload, ensure_ascii=False))


@tool
def upsert_local_kb_entry(
    raw_text: str = "",
    question: str = "",
    answer: str = "",
    keywords: Any = None,
    category: str = "",
    intent: str = "",
    sources: Any = None,
) -> str:
    """Append an authorized FAQ-style entry to the local HiFleet customer-support KB."""
    raw_text = str(raw_text or "")
    if not _has_update_command(raw_text) and not (question and answer):
        return json.dumps({"ok": False, "status": "rejected", "reason": "missing_explicit_kb_update_command"}, ensure_ascii=False)

    allowed, reason = _authorized(raw_text)
    if not allowed:
        _audit("rejected", {"question": question, "duplicate": False})
        return json.dumps({"ok": False, "status": "rejected", "reason": reason}, ensure_ascii=False)

    path = _kb_jsonl_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_items = _load_jsonl_items(path)
    batch_result = _upsert_mapping_entries(raw_text, existing_items, path)
    if batch_result is not None:
        return json.dumps(batch_result, ensure_ascii=False)

    parsed_question, parsed_answer = _split_question_answer(raw_text, question=question, answer=answer)
    if not parsed_question or len(parsed_question) < 4:
        return json.dumps({"ok": False, "status": "needs_more_info", "reason": "missing_question"}, ensure_ascii=False)
    if not parsed_answer or len(parsed_answer) < 20:
        return json.dumps({"ok": False, "status": "needs_more_info", "reason": "missing_standard_answer"}, ensure_ascii=False)

    clean_raw_text = _strip_text_key(raw_text)
    content_for_keywords = f"{parsed_question}\n{parsed_answer}\n{clean_raw_text}"
    preferred_keywords = _preferred_keywords(parsed_question, parsed_answer)
    normalized_keywords = preferred_keywords or _normalize_keywords(keywords, content_for_keywords)
    normalized_keywords = [
        item for item in normalized_keywords
        if normalize_query_text(item) != normalize_query_text(_extract_text_key(raw_text))
    ]
    source_list = [str(item).strip() for item in (sources if isinstance(sources, list) else []) if str(item).strip()]
    for url in _extract_sources(clean_raw_text + "\n" + parsed_answer):
        if url not in source_list:
            source_list.append(url)
    category_value, intent_value = _infer_category_intent(parsed_question, parsed_answer, category=category, intent=intent)

    duplicate = _duplicate_match(parsed_question, parsed_answer, normalized_keywords, existing_items)
    if duplicate.get("duplicate"):
        _audit("duplicate", {"question": parsed_question, "duplicate": True, "id": duplicate.get("id", "")})
        return json.dumps({"ok": False, "status": "duplicate", **duplicate}, ensure_ascii=False)

    entry = {
        "id": _next_kb_id(existing_items),
        "category": category_value,
        "intent": intent_value,
        "question": parsed_question,
        "answer": parsed_answer,
        "keywords": normalized_keywords,
        "related_topics": [],
        "sources": source_list,
        "escalate_when": ["页面实际选项与知识库描述不一致", "用户提供截图或更多上下文需要人工确认"],
    }
    _write_entries(path, [entry])
    verification = search_local_kb_structured(parsed_question, top_k=3)
    _audit("upserted", {"question": parsed_question, "duplicate": False, "id": entry["id"]})
    return json.dumps(
        {
            "ok": True,
            "status": "upserted",
            "id": entry["id"],
            "question": parsed_question,
            "keywords": normalized_keywords,
            "sources": source_list,
            "verification_can_answer": bool(verification.get("can_answer")),
            "recommended_test_query": parsed_question,
        },
        ensure_ascii=False,
    )


__all__ = ["upsert_local_kb_entry"]
