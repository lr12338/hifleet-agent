"""Public page verification and controlled browser-based web search helpers."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from urllib.parse import quote_plus, urlparse

import requests
from langchain.tools import tool

from skills.employee_workspace.tools import ARTIFACT_ROOT, _prepare_job_dir, _run_in_docker

logger = logging.getLogger(__name__)

AGENT_BROWSER_SESSION = "hifleet-cs-fallback"
AGENT_BROWSER_TIMEOUT_SEC = 25
AGENT_BROWSER_MAX_BODY_CHARS = 4000
PY_SANDBOX_SEARCH_NETWORK_MODE = os.getenv("HIFLEET_PY_SANDBOX_BROWSER_NETWORK_MODE", "bridge").strip() or "bridge"
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


def _is_hifleet_official_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "hifleet.com" or host.endswith(".hifleet.com")


def _page_match_reason(query: str, title: str, body: str, summary: str = "") -> str:
    haystack = f"{title} {body[:1000]} {summary}".lower()
    tokens = [token for token in _query_keywords(query) if len(token) >= 2]
    matched = [token for token in tokens if token.lower() in haystack]
    if matched:
        return "页面标题或正文匹配：" + "、".join(matched[:5])
    return "HiFleet 官方公开页面"


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
    candidate_sources = _bing_search_candidates(query) + _preferred_hifleet_candidates(query) if _needs_specific_hifleet_page(query) else _preferred_hifleet_candidates(query) + _bing_search_candidates(query)
    for item in candidate_sources:
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(item)
        if len(candidates) >= 5:
            break
    return candidates


def _needs_specific_hifleet_page(query: str) -> bool:
    q = (query or "").lower()
    markers = ["验证", "核验", "社区", "发布", "详细内容", "今日", "今天", "最新", "长江水位", "浏览器开始记忆"]
    return any(marker in q for marker in markers)


def _extract_json_payload(raw_stdout: str) -> dict[str, object]:
    value = (raw_stdout or "").strip()
    if not value:
        return {}
    candidates = re.findall(r"\{.*\}", value, flags=re.DOTALL)
    for item in reversed(candidates):
        try:
            payload = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _sandbox_search_script(query: str) -> str:
    query_literal = json.dumps(query, ensure_ascii=False)
    preferred_literal = json.dumps(PREFERRED_HIFLEET_PAGES, ensure_ascii=False)
    return f"""import json
import re
import urllib.parse
import urllib.request
from html import unescape
from urllib.parse import urlparse

QUERY = {query_literal}
HEADERS = {{"User-Agent": "HiFleetCustomerSupportSandbox/1.0"}}
PREFERRED = {preferred_literal}

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "hifleet.com" or host.endswith(".hifleet.com")

candidates = []
seen = set()
for item in PREFERRED:
    url = str(item.get("url", "")).strip()
    if url and allowed(url) and url not in seen:
        candidates.append({"url": url, "title": item.get("title", ""), "summary": "", "source": "sandbox_preferred"})
        seen.add(url)

bing_query = f'site:hifleet.com "HiFleet" "{{QUERY}}"'
search_url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(bing_query) + "&count=6&setlang=en"
try:
    html = fetch(search_url)
except Exception:
    html = ""

for match in re.finditer(r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<p>(.*?)</p>)?.*?</li>', html, flags=re.IGNORECASE | re.DOTALL):
    url = clean(match.group(1) or "")
    if not url or not allowed(url) or url in seen:
        continue
    title = clean(match.group(2) or "") or "HiFleet 页面"
    summary = clean(match.group(3) or "")
    candidates.append({"url": url, "title": title, "summary": summary[:400], "source": "sandbox_bing"})
    seen.add(url)
    if len(candidates) >= 5:
        break

print(json.dumps({"candidates": candidates[:5]}, ensure_ascii=False))
"""


def _sandbox_hifleet_candidates(query: str) -> list[dict[str, str]]:
    try:
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        job_dir, _, _, script_path = _prepare_job_dir()
        script_path.write_text(_sandbox_search_script(query), encoding="utf-8")
        runtime = _run_in_docker(
            job_dir,
            network_mode=PY_SANDBOX_SEARCH_NETWORK_MODE,
            extra_env={"PYTHONIOENCODING": "utf-8"},
        )
        if int(runtime.get("exit_code", 1)) != 0:
            logger.warning("sandbox hifleet search failed: %s", runtime.get("stderr", ""))
            return []
        payload = _extract_json_payload(str(runtime.get("stdout", "")))
        items = payload.get("candidates") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        normalized: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not _is_public_http_url(url) or "hifleet.com" not in url.lower() or url in seen_urls:
                continue
            seen_urls.add(url)
            normalized.append(
                {
                    "url": url,
                    "title": str(item.get("title", "")).strip() or "HiFleet 页面",
                    "summary": _normalize_page_text(str(item.get("summary", "")).strip()),
                    "source": str(item.get("source", "sandbox_python")).strip() or "sandbox_python",
                    "query": query,
                }
            )
            if len(normalized) >= 5:
                break
        return normalized
    except Exception as exc:
        logger.warning("sandbox hifleet search error: %s", exc)
        return []


def _format_browser_result(query: str, pages: list[dict[str, str]]) -> str:
    evidence_pages: list[dict[str, object]] = []
    for page in pages[:3]:
        title = page.get("title") or "HiFleet 页面"
        url = page.get("url", "")
        body = page.get("body", "")
        summary = page.get("summary", "")
        if not body and not summary:
            continue
        excerpt = _normalize_page_text(body or summary)[:800].rstrip()
        if not excerpt:
            continue
        evidence_pages.append(
            {
                "title": title,
                "url": url,
                "excerpt": excerpt,
                "match_reason": _page_match_reason(query, title, body, summary),
                "official": _is_hifleet_official_url(url),
            }
        )
    if not evidence_pages:
        return NO_HIT_TEXT
    return json.dumps(
        {
            "type": "hifleet_browser_evidence",
            "query": query,
            "source_scope": "hifleet_official_public_pages",
            "pages": evidence_pages,
        },
        ensure_ascii=False,
    )


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

    candidates = _candidate_urls(sanitized_query) if _needs_specific_hifleet_page(sanitized_query) else (_sandbox_hifleet_candidates(sanitized_query) or _candidate_urls(sanitized_query))
    if not candidates:
        candidates = _sandbox_hifleet_candidates(sanitized_query)
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
