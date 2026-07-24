from __future__ import annotations

import json
import re
from typing import Any


_HIGH_RISK = re.compile(r"(支持|权限|会员|价格|套餐|入口|按钮|部门|发布|制定|目的港|ETA|自动解析|立即生效|更新成功|没有数据|未找到)", re.I)
_SENTENCE = re.compile(r"[^。！？!?]*[。！？!?]|[^。！？!?]+$")


def _structured_evidence_text(item: dict[str, Any]) -> str:
    """Extract source content, excluding tool queries and control metadata."""
    fragments: list[str] = []

    def append_document_content(payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        entries = payload.get("items")
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("content", "snippet", "text"):
                value = entry.get(key)
                if value:
                    fragments.append(str(value))

    for fact in list(item.get("facts") or []):
        value = str(fact)
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            fragments.append(value)
        else:
            append_document_content(parsed)
    data = item.get("data")
    if isinstance(data, dict):
        append_document_content(data)
    return " ".join(fragments)


_NEGATION_PREFIXES = ("不", "没", "未", "非", "无", "否")
_NEGATION_WORDS = ("无法", "不能", "不可", "不可以", "没有", "并非", "并不", "未能", "暂不", "尚不")
_MIN_SUPPORT_PHRASE = 4


def _term_occurrences(term: str, text: str):
    start = 0
    while True:
        index = text.find(term, start)
        if index == -1:
            return
        yield index, text[max(0, index - 3):index]
        start = index + len(term)


def _is_negated_occurrence(prefix: str) -> bool:
    if not prefix:
        return False
    if prefix[-1] in _NEGATION_PREFIXES:
        return True
    return any(prefix.endswith(word) for word in _NEGATION_WORDS)


def _has_shared_phrase(sentence: str, evidence: str, min_len: int) -> bool:
    """True when evidence contains a contiguous sentence fragment of >=min_len chars.

    A lone keyword overlap (e.g. only "支持" shared) is not enough; the evidence must
    repeat a meaningful phrase from the claim, which blocks weakly-related evidence.
    """
    if len(sentence) < min_len:
        return False
    for start in range(len(sentence) - min_len + 1):
        if sentence[start:start + min_len] in evidence:
            return True
    return False


def _number_tokens(text: str) -> set[str]:
    return set(re.findall(r"\d+", text or ""))


def guard_claims(answer: str, observations: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Remove high-risk sentences that lack direct, non-contradicted evidence.

    A high-risk claim is kept only when the evidence positively (non-negated) repeats a
    meaningful phrase from the claim. Unrelated tool success, weakly-related evidence,
    and evidence that negates the claim all remain blocked. This is intentionally
    conservative: a keyword merely appearing in the evidence never proves a product
    capability, policy, attribution, UI entry, or completion claim.
    """
    evidence = " ".join(
        _structured_evidence_text(item)
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
        if not terms:
            kept.append(text)
            continue
        lowered = text.lower()
        contradiction = False
        positive_support = False
        for term in terms:
            occurrences = list(_term_occurrences(term, evidence))
            if not occurrences:
                continue
            if all(_is_negated_occurrence(prefix) for _, prefix in occurrences):
                # Evidence negates this term; a positive claim is contradicted.
                claim_occurrences = list(_term_occurrences(term, lowered))
                if any(not _is_negated_occurrence(prefix) for _, prefix in claim_occurrences):
                    contradiction = True
            else:
                positive_support = True
        phrase_aligned = _has_shared_phrase(lowered, evidence, _MIN_SUPPORT_PHRASE)
        claim_numbers = _number_tokens(lowered)
        evidence_numbers = _number_tokens(evidence)
        numeric_conflict = (
            phrase_aligned
            and bool(claim_numbers)
            and not claim_numbers.issubset(evidence_numbers)
        )
        if contradiction or not positive_support or not phrase_aligned or numeric_conflict:
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
