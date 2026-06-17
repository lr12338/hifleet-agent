"""Public page verification and controlled browser-based web search helpers."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from urllib.parse import quote_plus, urlparse

import requests
from langchain.tools import tool

logger = logging.getLogger(__name__)

AGENT_BROWSER_SESSION = "hifleet-cs-fallback"
AGENT_BROWSER_TIMEOUT_SEC = 25
AGENT_BROWSER_MAX_BODY_CHARS = 4000
NO_HIT_TEXT = "未检索到足够可信的信息"
BING_SEARCH_URL = "https://www.bing.com/search"
REQUESTS_HEADERS = {"User-Agent": "HiFleetCustomerSupport/1.0"}
PREFERRED_HIFLEET_PAGES = [
    {
        "url": "https://www.hifleet.com/",
        "title": "HiFleet 官网首页",
        "keywords": ["平台", "官网", "首页"],
    },
    {
        "url": "https://www.hifleet.com/wp/communities",
        "title": "HiFleet 官方社区",
        "keywords": ["社区", "communities", "community", "文章", "教程", "功能介绍"],
    },
    {
        "url": "https://www.hifleet.com/wp/community/",
        "title": "HiFleet 官方社区入口",
        "keywords": ["社区", "community", "文章", "教程", "功能介绍"],
    },
    {
        "url": "https://www.hifleet.com/data/index.html",
        "title": "HiFleet 数据服务",
        "keywords": ["数据", "data", "服务", "产品", "介绍"],
    },
    {
        "url": "https://www.hifleet.com/helpcenter/?i18n=en",
        "title": "HiFleet Help Center EN",
        "keywords": ["帮助", "help", "faq", "how to", "操作", "教程", "排障"],
    },
    {
        "url": "https://www.hifleet.com/account/index.html?type=account",
        "title": "HiFleet Account",
        "keywords": ["账号", "account", "登录", "权限", "会员", "专业版", "基础版", "免费版"],
    },
]


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    return host not in {"localhost", "127.0.0.1"} and not host.endswith(".local")


def _sanitize_query(query: str) -> str:
    value = (query or "").strip()[:500]
    if not value:
        return ""
    if any(char in value for char in [";", "|", "&", "$", "`", "(", ")"]):
        return ""
    return value


def _normalize_page_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    return value[:AGENT_BROWSER_MAX_BODY_CHARS]


def _strip_html_tags(text: str) -> str:
    value = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", value).strip()


def _run_agent_browser(*args: str, timeout: int = AGENT_BROWSER_TIMEOUT_SEC) -> str:
    cmd = ["agent-browser", "--session", AGENT_BROWSER_SESSION, "--max-output", str(AGENT_BROWSER_MAX_BODY_CHARS), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "agent-browser failed").strip()[:400])
    return (result.stdout or "").strip()


def _browser_capture_page_text(url: str) -> tuple[str, str]:
    _run_agent_browser("open", url)
    title = _run_agent_browser("get", "title")
    body = _run_agent_browser("get", "text", "body")
    body = _normalize_page_text(body)
    if body:
        return title.strip(), body
    snapshot = _normalize_page_text(_run_agent_browser("snapshot", "-c", "-d", "4"))
    return title.strip(), snapshot


def _query_keywords(query: str) -> list[str]:
    lowered = (query or "").lower()
    tokens = re.split(r"[\s,，。！？:/_-]+", lowered)
    return [token for token in tokens if token]


def _preferred_hifleet_candidates(query: str) -> list[dict[str, str]]:
    from skills.knowledge_qa.tools import _is_url_accessible

    tokens = _query_keywords(query)
    scored_pages: list[tuple[int, dict[str, str]]] = []
    for item in PREFERRED_HIFLEET_PAGES:
        score = 0
        page_tokens = [token.lower() for token in item.get("keywords", [])]
        for token in tokens:
            if any(token in page_token or page_token in token for page_token in page_tokens):
                score += 2
        scored_pages.append((score, item))

    candidates: list[dict[str, str]] = []
    ordered_pages = sorted(scored_pages, key=lambda value: value[0], reverse=True)
    positive_pages = [item for score, item in ordered_pages if score > 0]
    fallback_pages = [item for score, item in ordered_pages if score <= 0]
    for item in positive_pages + fallback_pages:
        url = item["url"]
        if not _is_public_http_url(url):
            continue
        if not _is_url_accessible(url):
            continue
        candidates.append(
            {
                "url": url,
                "title": item["title"],
                "summary": "",
                "source": "preferred_hifleet",
                "query": query,
            }
        )
        if len(candidates) >= 3:
            break
    return candidates


def _bing_search_candidates(query: str) -> list[dict[str, str]]:
    from skills.knowledge_qa.tools import _is_url_accessible

    bing_query = f'site:hifleet.com "{query}"'
    if "hifleet" not in query.lower() and "船队在线" not in query:
        bing_query = f'site:hifleet.com "HiFleet" "{query}"'
    search_url = f"{BING_SEARCH_URL}?q={quote_plus(bing_query)}&count=6&setlang=en"
    try:
        response = requests.get(search_url, timeout=10, headers=REQUESTS_HEADERS)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("agent_browser_deep_search bing error: %s", exc)
        return []

    html = response.text
    matches = re.finditer(
        r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<p>(.*?)</p>)?.*?</li>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for match in matches:
        url = str(match.group(1) or "").strip()
        if not _is_public_http_url(url) or "hifleet.com" not in url.lower():
            continue
        if url in seen_urls or not _is_url_accessible(url):
            continue
        seen_urls.add(url)
        candidates.append(
            {
                "url": url,
                "title": _strip_html_tags(match.group(2) or "") or "HiFleet 页面",
                "summary": _strip_html_tags(match.group(3) or ""),
                "source": "bing",
                "query": query,
            }
        )
        if len(candidates) >= 4:
            break
    return candidates


def _candidate_urls(query: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in _preferred_hifleet_candidates(query) + _bing_search_candidates(query):
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(item)
        if len(candidates) >= 5:
            break
    return candidates


def _format_browser_result(query: str, pages: list[dict[str, str]]) -> str:
    blocks = ["【公开网页深度检索】", f"问题：{query}"]
    for page in pages[:2]:
        title = page.get("title") or "公开页面"
        url = page.get("url", "")
        body = page.get("body", "")
        summary = page.get("summary", "")
        if not body and not summary:
            continue
        excerpt = body or summary
        excerpt = excerpt[:500].rstrip()
        blocks.append(f"\n来源：{title}")
        if excerpt:
            blocks.append(f"内容摘要：{excerpt}")
        if url:
            blocks.append(url)
    if len(blocks) <= 2:
        return NO_HIT_TEXT
    return "\n".join(blocks)


@tool
def verify_public_page(url: str) -> str:
    """Fetch a public HTTP(S) page title/snippet for customer-safe verification."""
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return json.dumps({"ok": False, "reason": "invalid_url"}, ensure_ascii=False)
    if parsed.hostname in {"localhost", "127.0.0.1"} or (parsed.hostname or "").endswith(".local"):
        return json.dumps({"ok": False, "reason": "internal_url_blocked"}, ensure_ascii=False)
    resp = requests.get(url, timeout=8, headers=REQUESTS_HEADERS)
    ok = 200 <= resp.status_code < 400
    text = resp.text[:5000] if ok else ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    snippet = re.sub(r"<[^>]+>", " ", text)
    snippet = re.sub(r"\s+", " ", snippet).strip()[:800]
    return json.dumps({"ok": ok, "status_code": resp.status_code, "title": title, "snippet": snippet, "url": url}, ensure_ascii=False)


@tool
def agent_browser_deep_search(query: str) -> str:
    """Fetch public-page evidence with agent-browser when knowledge search has no useful hit."""
    sanitized_query = _sanitize_query(query)
    if not sanitized_query:
        return NO_HIT_TEXT

    candidates = _candidate_urls(sanitized_query)
    if not candidates:
        return NO_HIT_TEXT

    pages: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            title, body = _browser_capture_page_text(candidate["url"])
        except FileNotFoundError:
            logger.warning("agent-browser CLI not found")
            break
        except Exception as exc:
            logger.warning("agent_browser_deep_search capture error for %s: %s", candidate["url"], exc)
            continue
        pages.append(
            {
                "title": title or candidate.get("title", ""),
                "url": candidate["url"],
                "body": body,
                "summary": candidate.get("summary", ""),
            }
        )
        if len(pages) >= 2:
            break

    return _format_browser_result(sanitized_query, pages)
