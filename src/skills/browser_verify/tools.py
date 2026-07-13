"""Public page verification and controlled browser-based web search helpers."""
from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import uuid
from hashlib import md5
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from langchain.tools import tool

from skills.employee_workspace.tools import ARTIFACT_ROOT, _prepare_job_dir, _run_in_docker
from skills.knowledge_qa.web_search_runtime import has_specific_fact, is_directory_page, is_specific_page, operation_evidence_count

logger = logging.getLogger(__name__)

AGENT_BROWSER_SESSION_PREFIX = "hifleet-cs"
AGENT_BROWSER_TIMEOUT_SEC = 25
AGENT_BROWSER_OPEN_TIMEOUT_SEC = 60
AGENT_BROWSER_MAX_BODY_CHARS = 4000
AGENT_BROWSER_SCREENSHOT_DIR = Path("/tmp/agent-browser-hifleet")
PY_SANDBOX_SEARCH_NETWORK_MODE = os.getenv("HIFLEET_PY_SANDBOX_BROWSER_NETWORK_MODE", "bridge").strip() or "bridge"
NO_HIT_TEXT = "未检索到足够可信的信息"
BING_SEARCH_URL = "https://www.bing.com/search"
REQUESTS_HEADERS = {"User-Agent": "HiFleetCustomerSupport/1.0"}
IMAGE_HEAVY_QUERY_MARKERS = ("图片", "图标", "截图", "界面", "海图", "标识", "图示", "符号", "image", "screenshot", "icon")
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
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    return _resolve_public_host(host)


def _resolve_public_host(host: str) -> bool:
    """Reject private, loopback, link-local, and otherwise non-public DNS answers."""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not parsed.is_global:
            return False
    return True


def _safe_public_get(url: str, *, timeout: int = 8, max_redirects: int = 3) -> requests.Response:
    """Fetch a public URL while validating every redirect hop before requesting it."""
    current_url = url
    for _ in range(max_redirects + 1):
        if not _is_public_http_url(current_url):
            raise ValueError("internal_url_blocked")
        response = requests.get(
            current_url,
            timeout=timeout,
            headers=REQUESTS_HEADERS,
            allow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("Location", "").strip()
            if not location:
                return response
            current_url = urljoin(current_url, location)
            continue
        return response
    raise ValueError("redirect_limit_exceeded")


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


def _normalize_keyword_token(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").lower()).strip()


def _strip_html_tags(text: str) -> str:
    value = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", value).strip()


def _merge_agent_browser_args(existing: str) -> str:
    merged = [part.strip() for part in re.split(r"[,\n]+", existing or "") if part.strip()]
    if os.name == "posix" and "--no-sandbox" not in merged:
        merged.append("--no-sandbox")
    return ",".join(merged)


def _new_agent_browser_session() -> str:
    return f"{AGENT_BROWSER_SESSION_PREFIX}-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}"


def _build_agent_browser_env(session: str) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENT_BROWSER_SESSION"] = session
    env["AGENT_BROWSER_ARGS"] = _merge_agent_browser_args(env.get("AGENT_BROWSER_ARGS", ""))
    return env


def _run_agent_browser(*args: str, timeout: int = AGENT_BROWSER_TIMEOUT_SEC, session: str = "") -> str:
    browser_session = session or _new_agent_browser_session()
    cmd = ["agent-browser", "--session", browser_session, "--max-output", str(AGENT_BROWSER_MAX_BODY_CHARS), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        env=_build_agent_browser_env(browser_session),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "agent-browser failed").strip()[:400])
    return (result.stdout or "").strip()


def _agent_browser_availability() -> dict[str, str | bool]:
    binary = shutil.which("agent-browser")
    if not binary:
        return {"available": False, "failure_code": "browser_cli_missing"}
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5, shell=False)
    except subprocess.TimeoutExpired:
        return {"available": False, "failure_code": "browser_doctor_failed"}
    if result.returncode != 0:
        return {"available": False, "failure_code": "browser_doctor_failed"}
    return {"available": True, "version": (result.stdout or "").strip()[:80]}


def _browser_failure_code(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "browser_cli_missing"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "browser_open_timeout"
    message = str(exc).lower()
    if "timeout" in message or "timed out" in message:
        return "browser_open_timeout"
    if "not found" in message or "no such file" in message:
        return "browser_cli_missing"
    return "browser_unknown_error"


def _query_contains_visual_markers(query: str) -> bool:
    lowered = (query or "").lower()
    return any(marker in lowered for marker in IMAGE_HEAVY_QUERY_MARKERS)


def _screenshot_output_path(url: str) -> str:
    AGENT_BROWSER_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url or "")
    slug = re.sub(r"[^0-9a-zA-Z_-]+", "-", f"{parsed.netloc}{parsed.path}".strip("-"))[:80].strip("-") or "page"
    digest = md5((url or "").encode("utf-8")).hexdigest()[:8]
    return str(AGENT_BROWSER_SCREENSHOT_DIR / f"{slug}-{digest}.png")


def _parse_int_output(value: str) -> int:
    match = re.search(r"-?\d+", value or "")
    return int(match.group(0)) if match else 0


def _strip_for_excerpt(text: str) -> str:
    value = _normalize_page_text(text)
    noisy_markers = [
        "论坛搜索",
        "最近主题",
        "最近留言",
        "论坛统计",
        "All forum topics",
        "Previous Topic",
        "Next Topic",
        "请 登录 或 注册 to reply to this topic.",
    ]
    cut_positions = [value.find(marker) for marker in noisy_markers if marker in value]
    if cut_positions:
        value = value[: min(cut_positions)].strip()
    return value


def _browser_capture_page_text(url: str, query: str = "", session: str = "") -> tuple[str, str, dict[str, str | int | bool]]:
    browser_session = session or _new_agent_browser_session()
    try:
        _run_agent_browser("open", url, timeout=AGENT_BROWSER_OPEN_TIMEOUT_SEC, session=browser_session)
    except RuntimeError:
        logger.debug("agent-browser open retry for %s", url)
        _run_agent_browser("open", url, timeout=AGENT_BROWSER_OPEN_TIMEOUT_SEC, session=browser_session)
    try:
        final_url = _run_agent_browser("get", "url", timeout=10, session=browser_session)
    except Exception as exc:
        raise RuntimeError("browser_parse_error") from exc
    if not _is_public_http_url(final_url):
        raise RuntimeError("browser_redirect_blocked")
    try:
        _run_agent_browser("wait", "--load", "networkidle", timeout=min(AGENT_BROWSER_TIMEOUT_SEC, 12), session=browser_session)
    except Exception:
        logger.debug("agent-browser wait networkidle failed for %s", url)
        try:
            _run_agent_browser("wait", "2000", timeout=5, session=browser_session)
        except Exception:
            logger.debug("agent-browser fixed wait failed for %s", url)
    try:
        title = _run_agent_browser("get", "title", session=browser_session)
    except Exception:
        logger.debug("agent-browser title failed for %s", url)
        title = ""
    try:
        body = _run_agent_browser("get", "text", "body", session=browser_session)
    except Exception:
        logger.debug("agent-browser body text failed for %s", url)
        try:
            _run_agent_browser("wait", "2000", timeout=5, session=browser_session)
            body = _run_agent_browser("get", "text", "body", session=browser_session)
        except Exception:
            body = ""
    body = _strip_for_excerpt(body)
    image_count = 0
    try:
        image_count = _parse_int_output(_run_agent_browser("get", "count", "img", timeout=10, session=browser_session))
    except Exception:
        logger.debug("agent-browser image count failed for %s", url)
    screenshot_path = ""
    needs_screenshot = image_count >= 3 or _query_contains_visual_markers(query)
    if needs_screenshot:
        screenshot_path = _screenshot_output_path(url)
        try:
            _run_agent_browser("screenshot", screenshot_path, timeout=min(AGENT_BROWSER_TIMEOUT_SEC, 20), session=browser_session)
        except Exception as exc:
            logger.debug("agent-browser screenshot failed for %s: %s", url, exc)
            screenshot_path = ""
    if body:
        return title.strip(), body, {"image_count": image_count, "screenshot_path": screenshot_path, "used_snapshot": False}
    try:
        snapshot = _strip_for_excerpt(_run_agent_browser("snapshot", "-c", "-d", "4", session=browser_session))
    except Exception:
        logger.debug("agent-browser snapshot failed for %s", url)
        snapshot = ""
    return title.strip(), snapshot, {"image_count": image_count, "screenshot_path": screenshot_path, "used_snapshot": True}


def _query_keywords(query: str) -> list[str]:
    lowered = (query or "").lower()
    tokens = re.split(r"[\s,，。！？:/_-]+", lowered)
    return [token for token in tokens if token]


def _query_variants(query: str, site_hint: str = "") -> list[str]:
    base = _sanitize_query(query)
    if not base:
        return []
    variants = [base]
    lowered = base.lower()
    if "hifleet" not in lowered and "船队在线" not in base:
        variants.append(f"HiFleet {base}")
    hint = _sanitize_query(site_hint)
    if hint and hint.lower() not in lowered:
        variants.append(f"{base} {hint}")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in variants:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            normalized.append(item)
    return normalized[:3]


def _is_hifleet_official_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "hifleet.com" or host.endswith(".hifleet.com")


def _candidate_keyword_score(query: str, title: str, summary: str, url: str = "") -> int:
    haystack = " ".join([title or "", summary or "", url or ""]).lower()
    tokens = [_normalize_keyword_token(token) for token in _query_keywords(query)]
    tokens = [token for token in tokens if len(token) >= 2]
    score = 0
    for token in tokens:
        if token and token in _normalize_keyword_token(haystack):
            score += 3 if len(token) >= 4 else 2
    return score


def _official_page_rank(url: str) -> int:
    if not _is_hifleet_official_url(url):
        return 0
    parsed = urlparse(url or "")
    path = (parsed.path or "/").lower()
    if "/wp/communities" in path or "/wp/community" in path or "helpcenter" in path:
        return 3
    if path and path != "/":
        return 2
    return 1


def _candidate_priority(candidate: dict[str, str], query: str) -> tuple[int, int, int, int]:
    url = str(candidate.get("url", "")).strip()
    title = str(candidate.get("title", "")).strip()
    summary = str(candidate.get("summary", "")).strip()
    source = str(candidate.get("source", "")).strip()
    official_rank = 2 if _is_hifleet_official_url(url) else 0
    page_rank = _official_page_rank(url)
    source_rank = 2 if source.startswith("preferred") or source.startswith("router_target") else 1 if source.startswith("sandbox") or source == "bing" else 0
    keyword_rank = _candidate_keyword_score(query, title, summary, url)
    return (official_rank, page_rank, keyword_rank, source_rank)


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

    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    search_specs: list[tuple[str, str]] = []
    for variant in _query_variants(query, "HiFleet"):
        official_query = f'site:hifleet.com "{variant}"'
        if "hifleet" not in variant.lower() and "船队在线" not in variant:
            official_query = f'site:hifleet.com "HiFleet" "{variant}"'
        search_specs.append((variant, official_query))
    for variant in _query_variants(query, "HiFleet"):
        public_query = f'"HiFleet" "{variant}"'
        if public_query not in {item[1] for item in search_specs}:
            search_specs.append((variant, public_query))

    for variant, bing_query in search_specs:
        search_url = f"{BING_SEARCH_URL}?q={quote_plus(bing_query)}&count=8&setlang=en"
        try:
            response = requests.get(search_url, timeout=10, headers=REQUESTS_HEADERS)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("agent_browser_deep_search bing error: %s", exc)
            continue

        html = response.text
        matches = re.finditer(
            r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a>.*?(?:<p>(.*?)</p>)?.*?</li>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for match in matches:
            url = str(match.group(1) or "").strip()
            if not _is_public_http_url(url):
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
                    "query": bing_query,
                }
            )
            if len(candidates) >= 8:
                return sorted(candidates, key=lambda item: _candidate_priority(item, query), reverse=True)[:5]
    candidates = sorted(candidates, key=lambda item: _candidate_priority(item, query), reverse=True)
    official = [item for item in candidates if _is_hifleet_official_url(item.get("url", ""))]
    fallback = [item for item in candidates if not _is_hifleet_official_url(item.get("url", ""))]
    return (official + fallback)[:5]


def _candidate_urls(query: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    has_hifleet_scope = "hifleet" in (query or "").lower() or "船队在线" in (query or "")
    needs_specific_page = _needs_specific_hifleet_page(query)
    if needs_specific_page:
        candidate_sources = _bing_search_candidates(query) + _preferred_hifleet_candidates(query)
    elif has_hifleet_scope:
        candidate_sources = _preferred_hifleet_candidates(query) + _bing_search_candidates(query)
    else:
        candidate_sources = _bing_search_candidates(query)
    for item in candidate_sources:
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(item)
        if len(candidates) >= 5:
            break
    return sorted(candidates, key=lambda item: _candidate_priority(item, query), reverse=True)


def _explicit_target_url_candidates(target_urls: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in re.split(r"[\n|]+", target_urls or ""):
        url = raw.strip()
        if not url or url in seen or not _is_public_http_url(url):
            continue
        seen.add(url)
        candidates.append(
            {
                "url": url,
                "title": "HiFleet 候选页面",
                "summary": "",
                "source": "router_target",
                "query": "",
            }
        )
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
    return rf"""import json
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
        candidates.append({{"url": url, "title": item.get("title", ""), "summary": "", "source": "sandbox_preferred"}})
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
    candidates.append({{"url": url, "title": title, "summary": summary[:400], "source": "sandbox_bing"}})
    seen.add(url)
    if len(candidates) >= 5:
        break

print(json.dumps({{"candidates": candidates[:5]}}, ensure_ascii=False))
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
        specific_page = is_specific_page(url)
        directory_page = is_directory_page(url, title, excerpt)
        query_tokens = [token for token in _query_keywords(query) if len(_normalize_keyword_token(token)) >= 2]
        normalized_haystack = _normalize_keyword_token(f"{title} {excerpt} {url}")
        matched_tokens = [token for token in query_tokens if _normalize_keyword_token(token) in normalized_haystack]
        coverage = len(matched_tokens) / max(1, len(query_tokens))
        fact_count = 1 if has_specific_fact(excerpt) else 0
        step_count = operation_evidence_count(excerpt)
        body_quality = "good" if len(body.strip()) >= 120 else "partial" if body.strip() else "empty"
        can_support_answer = bool(
            _is_hifleet_official_url(url)
            and specific_page
            and not directory_page
            and body_quality != "empty"
            and coverage > 0
            and (fact_count > 0 or step_count >= 2)
        )
        evidence_pages.append(
            {
                "title": title,
                "url": url,
                "excerpt": excerpt,
                "match_reason": _page_match_reason(query, title, body, summary),
                "official": _is_hifleet_official_url(url),
                "source": page.get("source", ""),
                "source_query": page.get("query", ""),
                "used_snapshot": bool(page.get("used_snapshot")),
                "image_count": int(page.get("image_count", 0) or 0),
                "screenshot_path": page.get("screenshot_path", ""),
                "specific_page": specific_page,
                "query_term_coverage": round(coverage, 3),
                "body_quality": body_quality,
                "fact_evidence_count": fact_count,
                "step_evidence_count": step_count,
                "relevance_score": round(min(1.0, coverage + (0.2 if can_support_answer else 0.0)), 3),
                "can_support_answer": can_support_answer,
            }
        )
    if not evidence_pages:
        return NO_HIT_TEXT
    return json.dumps(
        {
            "type": "hifleet_browser_evidence",
            "status": "ok" if any(page["can_support_answer"] for page in evidence_pages) else "browser_irrelevant_page",
            "query": query,
            "source_scope": "hifleet_official_public_pages",
            "search_strategy": {
                "keywords": _query_keywords(query)[:8],
                "variants": _query_variants(query, "HiFleet"),
                "official_first": True,
                "image_support": True,
            },
            "pages": evidence_pages,
        },
        ensure_ascii=False,
    )


@tool
def verify_public_page(url: str) -> str:
    """Fetch a public HTTP(S) page title/snippet for customer-safe verification."""
    if not _is_public_http_url(url):
        return json.dumps({"ok": False, "reason": "invalid_url"}, ensure_ascii=False)
    try:
        resp = _safe_public_get(url)
    except ValueError as exc:
        return json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False)
    except requests.RequestException:
        return json.dumps({"ok": False, "reason": "fetch_failed"}, ensure_ascii=False)
    ok = 200 <= resp.status_code < 400
    text = resp.text[:5000] if ok else ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    snippet = re.sub(r"<[^>]+>", " ", text)
    snippet = re.sub(r"\s+", " ", snippet).strip()[:800]
    return json.dumps({"ok": ok, "status_code": resp.status_code, "title": title, "snippet": snippet, "url": url}, ensure_ascii=False)


@tool
def agent_browser_deep_search(query: str, target_urls: str = "", site_hint: str = "") -> str:
    """最后一轮网页验证工具：关键词扩展、候选链接筛选、正文解析、必要时截图取证。"""
    sanitized_query = _sanitize_query(query)
    if not sanitized_query:
        return json.dumps({"type": "hifleet_browser_evidence", "status": "invalid_query", "summary": NO_HIT_TEXT, "pages": []}, ensure_ascii=False)

    availability = _agent_browser_availability()
    if not availability.get("available"):
        return json.dumps(
            {
                "type": "hifleet_browser_evidence",
                "status": str(availability["failure_code"]),
                "summary": NO_HIT_TEXT,
                "pages": [],
                "runtime": availability,
            },
            ensure_ascii=False,
        )

    explicit_targets = _explicit_target_url_candidates(target_urls)
    has_hifleet_scope = "hifleet" in sanitized_query.lower() or "hifleet" in (site_hint or "").lower() or _needs_specific_hifleet_page(sanitized_query)

    candidates = explicit_targets
    if not candidates:
        candidates = _candidate_urls(sanitized_query)
    if not candidates and has_hifleet_scope:
        candidates = _sandbox_hifleet_candidates(sanitized_query)
    if not candidates:
        return json.dumps({"type": "hifleet_browser_evidence", "status": "no_candidate", "summary": NO_HIT_TEXT, "pages": []}, ensure_ascii=False)

    pages: list[dict[str, str]] = []
    failures: list[str] = []
    browser_session = _new_agent_browser_session()
    try:
        for candidate in candidates:
            try:
                title, body, capture_meta = _browser_capture_page_text(candidate["url"], sanitized_query, session=browser_session)
            except Exception as exc:
                code = _browser_failure_code(exc)
                logger.warning("agent_browser_deep_search capture %s for %s", code, candidate["url"])
                failures.append(code)
                continue
            if not body.strip() and not candidate.get("summary"):
                failures.append("browser_empty_body")
                continue
            pages.append(
                {
                    "title": title or candidate.get("title", ""),
                    "url": candidate["url"],
                    "body": body,
                    "summary": candidate.get("summary", ""),
                    "source": candidate.get("source", ""),
                    "query": candidate.get("query", sanitized_query),
                    "used_snapshot": bool(capture_meta.get("used_snapshot")),
                    "image_count": int(capture_meta.get("image_count", 0) or 0),
                    "screenshot_path": str(capture_meta.get("screenshot_path", "") or ""),
                }
            )
            if len(pages) >= 2:
                break
    finally:
        try:
            _run_agent_browser("close", timeout=5, session=browser_session)
        except Exception:
            logger.debug("agent-browser close failed for session %s", browser_session)

    if not pages:
        return json.dumps(
            {
                "type": "hifleet_browser_evidence",
                "status": failures[0] if failures else "browser_no_candidates",
                "pages": [],
                "failure_count": len(failures),
                "failure_codes": failures[:3],
                "runtime": availability,
            },
            ensure_ascii=False,
        )
    return _format_browser_result(sanitized_query, pages)
