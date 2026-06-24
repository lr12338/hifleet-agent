from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

LOCAL_KB_TOP_K_DEFAULT = int(os.getenv("HIFLEET_LOCAL_KB_TOP_K", "5"))
LOCAL_KB_STRONG_MATCH_SCORE = float(os.getenv("HIFLEET_LOCAL_KB_STRONG_MATCH_SCORE", "0.58"))
LOCAL_KB_WEAK_MATCH_SCORE = float(os.getenv("HIFLEET_LOCAL_KB_WEAK_MATCH_SCORE", "0.33"))

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
    return [token for token in re.split(r"[\s/|_-]+", normalized) if token]


def local_kb_paths() -> dict[str, list[Path]]:
    rag_root = workspace_root() / "docs" / "RAG"
    return {
        "faq_jsonl": [rag_root / "hifleet_cs_outputs" / "客服知识库结构化.jsonl"],
        "faq_markdown": [
            rag_root / "hifleet_cs_outputs" / "客服问答对.md",
            rag_root / "hifleet_cs_outputs" / "FAQ检索词.md",
        ],
        "wiki": sorted((rag_root / "hifleet_cs_wiki").glob("*.md")),
        "product_doc": sorted((rag_root / "raw" / "产品文档").glob("*.md")),
    }


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
            title = path.stem
            data[kind].append(
                {
                    "id": title,
                    "title": title,
                    "content": text,
                    "searchable": f"{title}\n{text}",
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


def score_local_kb_item(query: str, item: dict[str, Any]) -> float:
    tokens = keyword_tokens(query)
    if not tokens:
        return 0.0
    haystack = normalize_query_text(item.get("searchable", ""))
    title = normalize_query_text(item.get("title", ""))
    score = 0.0
    for token in tokens:
        if token in title:
            score += 0.35
        elif token in haystack:
            score += 0.22
    joined_query = normalize_query_text(query)
    if joined_query and joined_query in haystack:
        score += 0.25
    if item.get("source_type") == "faq":
        score += 0.08
    return min(score, 1.0)


def search_local_kb_structured(query: str, top_k: int = LOCAL_KB_TOP_K_DEFAULT) -> dict[str, Any]:
    index = load_local_kb_index()
    results: list[dict[str, Any]] = []
    for bucket in ("faq", "wiki", "product_doc"):
        for item in index.get(bucket, []):
            score = score_local_kb_item(query, item)
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
    strong_faq = any(item["source_type"] == "faq" and item["score"] >= LOCAL_KB_STRONG_MATCH_SCORE for item in results)
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
