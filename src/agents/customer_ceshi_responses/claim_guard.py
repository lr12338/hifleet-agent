from __future__ import annotations

import re
from typing import Any


_HIGH_RISK = re.compile(r"(支持|权限|会员|价格|套餐|入口|按钮|部门|发布|制定|目的港|ETA|自动解析|立即生效|更新成功|没有数据|未找到)", re.I)
_SENTENCE = re.compile(r"[^。！？!?]*[。！？!?]|[^。！？!?]+$")


def guard_claims(answer: str, observations: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Remove high-risk sentences that lack direct lexical evidence.

    This is intentionally conservative: unrelated tool success never becomes proof of a
    product capability, policy, attribution, UI entry, or completion claim.
    """
    evidence = " ".join(
        " ".join(str(fact) for fact in item.get("facts", []))
        for item in observations
        if item.get("status") in {"success", "partial"}
    ).lower()
    kept: list[str] = []
    blocked: list[str] = []
    for sentence in re.split(r"(?<=[。！？!?])", answer or ""):
        text = sentence.strip()
        if not text:
            continue
        terms = [term.lower() for term in _HIGH_RISK.findall(text)]
        if terms and not any(term in evidence for term in terms):
            blocked.append(text)
            continue
        kept.append(text)
    if blocked and not kept:
        kept.append("该结论目前缺少可直接核验的依据，我可以继续查询官方资料或请您补充页面信息。")
    return "".join(kept), blocked


def limit_reply(answer: str, *, max_chinese_chars: int = 180) -> str:
    """Keep customer replies compact without splitting a sentence when possible."""
    text = (answer or "").strip()
    if sum("\u4e00" <= char <= "\u9fff" for char in text) <= max_chinese_chars:
        return text
    kept: list[str] = []
    count = 0
    for sentence in _SENTENCE.findall(text):
        sentence_count = sum("\u4e00" <= char <= "\u9fff" for char in sentence)
        if kept and count + sentence_count > max_chinese_chars:
            break
        if not kept and sentence_count > max_chinese_chars:
            clipped: list[str] = []
            for char in sentence:
                if "\u4e00" <= char <= "\u9fff":
                    count += 1
                if count > max_chinese_chars:
                    break
                clipped.append(char)
            return "".join(clipped).rstrip("，、；;：:") + "。"
        kept.append(sentence)
        count += sentence_count
    return "".join(kept).strip() or text
