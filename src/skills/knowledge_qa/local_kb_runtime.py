from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

LOCAL_KB_TOP_K_DEFAULT = int(os.getenv("HIFLEET_LOCAL_KB_TOP_K", "5"))
LOCAL_KB_STRONG_MATCH_SCORE = float(os.getenv("HIFLEET_LOCAL_KB_STRONG_MATCH_SCORE", "0.58"))
LOCAL_KB_WEAK_MATCH_SCORE = float(os.getenv("HIFLEET_LOCAL_KB_WEAK_MATCH_SCORE", "0.25"))
_BRAND_ONLY_TOKENS = {"hifleet", "船队在线"}

_LOCAL_KB_LOCK = threading.Lock()
_LOCAL_KB_INDEX: dict[str, list[dict[str, Any]]] | None = None


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


def normalize_query_text(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"[，。！？、,.!?：:;；（）()【】\[\]\"'`]+", " ", value).strip()


def keyword_tokens(text: str) -> list[str]:
    normalized = normalize_query_text(text)
    tokens: list[str] = []
    for token in re.split(r"[\s/|_-]+", normalized):
        if not token:
            continue
        tokens.append(token)
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", token):
            for size in range(2, min(4, len(chunk)) + 1):
                tokens.extend(chunk[index : index + size] for index in range(len(chunk) - size + 1))
    return list(dict.fromkeys(tokens))


def local_kb_paths() -> dict[str, list[Path]]:
    rag_root = workspace_root() / "docs" / "RAG"
    return {
        "faq_jsonl": [rag_root / "hifleet_cs_outputs" / "客服知识库结构化.jsonl"],
        "faq_markdown": [
            rag_root / "hifleet_cs_outputs" / "客服问答对.md",
            rag_root / "hifleet_cs_outputs" / "FAQ检索词.md",
        ],
        "wiki": sorted((rag_root / "hifleet_cs_wiki").glob("*.md")),
        "product_doc": sorted(
            set((rag_root / "raw" / "产品文档").rglob("*.md"))
            | set((rag_root / "raw" / "产品指导文档").rglob("*.md"))
        ),
    }


def _document_sections(text: str, fallback_title: str) -> list[tuple[str, str]]:
    heading_pattern = re.compile(r"(?m)^(#{1,6}\s+.+)$")
    matches = list(heading_pattern.finditer(text))
    if not matches:
        return [(fallback_title, text)]
    levels = [len(match.group(1)) - len(match.group(1).lstrip("#")) for match in matches]
    minimum_level = min(levels)
    has_nested_sections = any(level > minimum_level for level in levels)
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        level = levels[index]
        if has_nested_sections and level == minimum_level:
            continue
        end = len(text)
        for next_index in range(index + 1, len(matches)):
            if levels[next_index] <= level:
                end = matches[next_index].start()
                break
        heading = re.sub(r"^#+\s*", "", match.group(1)).strip()
        content = text[match.start() : end].strip()
        if content:
            sections.append((heading or fallback_title, content))
    return sections or [(fallback_title, text)]


def build_local_kb_index() -> dict[str, list[dict[str, Any]]]:
    data = {"faq": [], "wiki": [], "product_doc": []}
    paths = local_kb_paths()
    for path in paths["faq_jsonl"]:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                question = str(item.get("question", "")).strip()
                answer = str(item.get("answer", "")).strip()
                keywords = [str(v).strip() for v in list(item.get("keywords") or []) if str(v).strip()]
                searchable = " ".join([question, answer, " ".join(keywords)])
                data["faq"].append(
                    {
                        "id": str(item.get("id", "")),
                        "title": question,
                        "content": answer,
                        "searchable": searchable,
                        "source_type": "faq",
                        "source": ",".join(item.get("sources") or []),
                        "keywords": keywords,
                    }
                )
    for kind in ("wiki", "product_doc"):
        for path in paths[kind]:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            document_title = path.stem
            for section_index, (section_title, section_content) in enumerate(_document_sections(text, document_title), start=1):
                title = document_title if section_title == document_title else f"{document_title} · {section_title}"
                data[kind].append(
                    {
                        "id": f"{document_title}:{section_index}",
                        "title": title,
                        "content": section_content,
                        "searchable": f"{title}\n{section_content}",
                        "source_type": kind,
                        "source": str(path.relative_to(workspace_root())),
                        "keywords": [],
                    }
                )
    return data


def load_local_kb_index() -> dict[str, list[dict[str, Any]]]:
    global _LOCAL_KB_INDEX
    with _LOCAL_KB_LOCK:
        if _LOCAL_KB_INDEX is None:
            _LOCAL_KB_INDEX = build_local_kb_index()
        return _LOCAL_KB_INDEX


def reset_local_kb_index() -> None:
    global _LOCAL_KB_INDEX
    with _LOCAL_KB_LOCK:
        _LOCAL_KB_INDEX = None


def local_kb_index_summary() -> dict[str, Any]:
    """Return a small operational summary without exposing document content."""
    index = load_local_kb_index()
    return {
        "loaded": True,
        "source_counts": {name: len(items) for name, items in index.items()},
        "document_count": sum(len(items) for items in index.values()),
        "includes_product_guidance": bool(index.get("product_doc")),
    }


def score_local_kb_item(query: str, item: dict[str, Any], *, token_weights: dict[str, float] | None = None) -> float:
    tokens = keyword_tokens(query)
    if not tokens:
        return 0.0
    haystack = normalize_query_text(item.get("searchable", ""))
    title = normalize_query_text(item.get("title", ""))
    score = 0.0
    topical_match = False
    for token in tokens:
        if token in _BRAND_ONLY_TOKENS:
            continue
        weight = float((token_weights or {}).get(token, 1.0))
        if token in title:
            score += 0.45 * weight
            topical_match = True
        elif token in haystack:
            score += 0.30 * weight
            topical_match = True
    if not topical_match:
        return 0.0
    joined_query = normalize_query_text(query)
    non_brand_query = " ".join(token for token in keyword_tokens(joined_query) if token not in _BRAND_ONLY_TOKENS)
    if non_brand_query and non_brand_query in haystack:
        score += 0.25
    if item.get("source_type") == "faq":
        score += 0.08
    return score


def _query_token_weights(index: dict[str, list[dict[str, Any]]], query: str) -> dict[str, float]:
    tokens = [token for token in keyword_tokens(query) if token not in _BRAND_ONLY_TOKENS]
    documents = [item for bucket in index.values() for item in bucket]
    total_documents = max(1, len(documents))
    weights: dict[str, float] = {}
    for token in tokens:
        document_frequency = sum(
            1
            for item in documents
            if token in normalize_query_text(item.get("searchable", ""))
        )
        weights[token] = 1.0 + math.log((total_documents + 1) / (document_frequency + 1))
    return weights


def _faq_has_title_topic_coverage(query: str, item: dict[str, Any]) -> bool:
    title = normalize_query_text(item.get("title", ""))
    matched = {
        token
        for token in keyword_tokens(query)
        if token not in _BRAND_ONLY_TOKENS and len(token) >= 2 and token in title
    }
    return len(matched) >= 2 or any(len(token) >= 4 for token in matched)


def search_local_kb_structured(query: str, top_k: int = LOCAL_KB_TOP_K_DEFAULT) -> dict[str, Any]:
    index = load_local_kb_index()
    token_weights = _query_token_weights(index, query)
    results: list[dict[str, Any]] = []
    for bucket in ("faq", "wiki", "product_doc"):
        for item in index.get(bucket, []):
            score = score_local_kb_item(query, item, token_weights=token_weights)
            if score < LOCAL_KB_WEAK_MATCH_SCORE:
                continue
            results.append(
                {
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "content": str(item.get("content", ""))[:1200],
                    "source": item.get("source", ""),
                    "source_type": item.get("source_type", bucket),
                    "score": round(score, 4),
                }
            )
    results.sort(key=lambda item: (item.get("score", 0.0), 1 if item.get("source_type") == "faq" else 0), reverse=True)
    results = results[: max(1, top_k)]
    strong_faq = any(
        item["source_type"] == "faq"
        and item["score"] >= LOCAL_KB_STRONG_MATCH_SCORE
        and _faq_has_title_topic_coverage(query, item)
        for item in results
    )
    return {
        "tool": "local_kb_search",
        "query": query,
        "status": "ok",
        "can_answer": strong_faq,
        "should_continue": not strong_faq,
        "continue_with": "none" if strong_faq else "web_search",
        "confidence": "high" if strong_faq else ("medium" if results else "low"),
        "summary": f"本地知识库命中 {len(results)} 条结果，优先命中 FAQ。" if results else "本地知识库未命中可直接回答的结果。",
        "items": results,
        "best_urls": [],
        "recommended_next_action": "直接基于 FAQ 回答用户" if strong_faq else "继续调用 web_search 获取官方或公开网页证据",
        "trace": {
            "result_count": len(results),
            "strong_faq": strong_faq,
            "source_breakdown": {
                "faq": sum(1 for item in results if item["source_type"] == "faq"),
                "wiki": sum(1 for item in results if item["source_type"] == "wiki"),
                "product_doc": sum(1 for item in results if item["source_type"] == "product_doc"),
            },
        },
    }


def format_local_kb_response(payload: dict[str, Any], help_center_url: str) -> str:
    items = list(payload.get("items") or [])
    if not items:
        return (
            "抱歉，我暂时没有在本地知识库中找到关于这个问题的准确答案。\n\n"
            f"建议继续核查官方资料：{help_center_url}"
        )
    parts = ["【优先匹配 - FAQ/标准回复】" if payload.get("can_answer") else "【主题说明（补充参考）】"]
    for item in items[:2]:
        parts.append(f"\n相关度: {float(item.get('score', 0.0)):.2f}")
        parts.append(str(item.get("content", "")).strip())
    return "\n".join(parts).strip()
