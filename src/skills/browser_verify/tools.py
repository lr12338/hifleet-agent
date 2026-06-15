"""Public page verification helper."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import requests
from langchain.tools import tool


@tool
def verify_public_page(url: str) -> str:
    """Fetch a public HTTP(S) page title/snippet for customer-safe verification."""
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return json.dumps({"ok": False, "reason": "invalid_url"}, ensure_ascii=False)
    if parsed.hostname in {"localhost", "127.0.0.1"} or (parsed.hostname or "").endswith(".local"):
        return json.dumps({"ok": False, "reason": "internal_url_blocked"}, ensure_ascii=False)
    resp = requests.get(url, timeout=8, headers={"User-Agent": "HiFleetCustomerSupport/1.0"})
    ok = 200 <= resp.status_code < 400
    text = resp.text[:5000] if ok else ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    snippet = re.sub(r"<[^>]+>", " ", text)
    snippet = re.sub(r"\s+", " ", snippet).strip()[:800]
    return json.dumps({"ok": ok, "status_code": resp.status_code, "title": title, "snippet": snippet, "url": url}, ensure_ascii=False)
