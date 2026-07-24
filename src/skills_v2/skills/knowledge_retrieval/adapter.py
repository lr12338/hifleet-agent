"""V2 knowledge_retrieval adapter: exposes only ``local_kb_search``.

Read-only local knowledge-base retrieval. Returns evidence id, source, match
score and summary. Never performs a web search and never treats a weak match as
a confirmed product conclusion.
"""
from __future__ import annotations

import json

from langchain.tools import tool

from .local_kb_runtime import LOCAL_KB_TOP_K_DEFAULT, search_local_kb_structured


@tool
def local_kb_search(query: str, top_k: int = LOCAL_KB_TOP_K_DEFAULT) -> str:
    """检索本地 docs/RAG 知识库，并返回结构化结果供 agent 判断是否继续联网搜索。"""
    payload = search_local_kb_structured(query, top_k=top_k)
    return json.dumps(payload, ensure_ascii=False)


__all__ = ["local_kb_search"]
