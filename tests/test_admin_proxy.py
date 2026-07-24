import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi import HTTPException

from admin_api import service
from admin_api.schemas import AdminTestRunRequest


class _FakeRequest:
    def __init__(self, disconnected: bool = False) -> None:
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


def _make_handler(mode: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _read_body(self) -> bytes:
            n = int(self.headers.get("content-length", 0))
            return self.rfile.read(n) if n else b""

        def do_POST(self):  # noqa: N802
            self._read_body()
            if mode == "run":
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("authorization", "Bearer secret")  # must be redacted
                self.send_header("x-run-id", "r1")
                self.end_headers()
                self.wfile.write(json.dumps({"run_id": "r1", "answer": "hi"}).encode())
            elif mode == "stream":
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'event: debug\ndata: {"type":"run.started"}\n\n')
                self.wfile.write(b'event: debug\ndata: {"type":"run.completed"}\n\n')
            elif mode == "error":
                self.send_response(500)
                self.send_header("content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"upstream boom")
            elif mode == "cancel":
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "run_id": self.path.split("/")[-1]}).encode())
            elif mode == "slow":
                import time as _t
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                _t.sleep(30)  # never sends data; tests disconnect/timeout
            else:
                self.send_response(404)
                self.end_headers()

    return Handler


@pytest.fixture
def http_server():
    servers = {}

    def start(mode: str):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mode))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers[mode] = srv
        return f"http://127.0.0.1:{port}"

    yield start

    for srv in servers.values():
        srv.shutdown()


@pytest.fixture(autouse=True)
def _allowlist_env(monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:10123")
    monkeypatch.delenv("AGENT_ALLOWLIST", raising=False)


# --- SSRF allowlist (pure) ---
def test_ssrf_rejects_arbitrary_public_target():
    with pytest.raises(HTTPException) as exc:
        service._resolve_allowed_target_url(AdminTestRunRequest(payload={}, target_agent_url="http://evil.example.com"))
    assert exc.value.status_code == 400


def test_ssrf_rejects_cloud_metadata_ip():
    with pytest.raises(HTTPException) as exc:
        service._resolve_allowed_target_url(AdminTestRunRequest(payload={}, target_agent_url="http://169.254.169.254/latest/meta-data"))
    assert exc.value.status_code == 400


def test_ssrf_allows_configured_target(monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:9999")
    url = service._resolve_allowed_target_url(AdminTestRunRequest(payload={}, target_agent_url="http://127.0.0.1:9999"))
    assert url == "http://127.0.0.1:9999"


def test_ssrf_uses_default_when_no_target(monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:4242")
    assert service._resolve_allowed_target_url(AdminTestRunRequest(payload={})) == "http://127.0.0.1:4242"


def test_ssrf_alllist_extra_host_allowed(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOWLIST", "http://10.0.0.5:10123")
    url = service._resolve_allowed_target_url(AdminTestRunRequest(payload={}, target_agent_url="http://10.0.0.5:10123"))
    assert url == "http://10.0.0.5:10123"


def test_graded_timeout_has_separate_components():
    t = service._graded_timeout(AdminTestRunRequest(payload={}, timeout_s=120))
    assert t.connect == 10
    assert t.read == 120
    assert t.write == 30
    assert t.pool == 10


# --- /run sync proxy ---
def test_proxy_run_returns_latency_run_id_and_redacted_headers(http_server, monkeypatch):
    base = http_server("run")
    monkeypatch.setenv("AGENT_BASE_URL", base)
    req = AdminTestRunRequest(endpoint="/run", payload={"messages": [{"role": "user", "content": "hi"}]}, run_id="r1")
    result = asyncio.run(service.proxy_test_run(req))
    assert result["status_code"] == 200
    assert result["run_id"] == "r1"
    assert isinstance(result["latency_ms"], int) and result["latency_ms"] >= 0
    assert result["headers"].get("authorization") == "***"
    assert result["body"]["answer"] == "hi"


# --- /stream_run proxy ---
def test_stream_proxy_passes_through_sse_and_status(http_server, monkeypatch):
    base = http_server("stream")
    monkeypatch.setenv("AGENT_BASE_URL", base)
    req = AdminTestRunRequest(endpoint="/stream_run", payload={"messages": [{"role": "user", "content": "hi"}]}, run_id="r2", stream=True)

    async def run():
        response, iterator = await service.stream_test_run(req, _FakeRequest(disconnected=False))
        assert response.status_code == 200
        chunks = []
        async for chunk in iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    blob = asyncio.run(run())
    assert b"run.started" in blob and b"run.completed" in blob


def test_stream_proxy_rejects_upstream_error_status(http_server, monkeypatch):
    base = http_server("error")
    monkeypatch.setenv("AGENT_BASE_URL", base)
    req = AdminTestRunRequest(endpoint="/stream_run", payload={"messages": [{"role": "user", "content": "hi"}]}, stream=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service.stream_test_run(req, _FakeRequest(disconnected=False)))
    assert exc.value.status_code == 500


def test_stream_proxy_detects_client_disconnect(http_server, monkeypatch):
    base = http_server("stream")
    monkeypatch.setenv("AGENT_BASE_URL", base)
    req = AdminTestRunRequest(endpoint="/stream_run", payload={"messages": [{"role": "user", "content": "hi"}]}, run_id="r3", stream=True)

    async def run():
        response, iterator = await service.stream_test_run(req, _FakeRequest(disconnected=True))
        chunks = []
        async for chunk in iterator:
            chunks.append(chunk)
        return chunks

    assert asyncio.run(run()) == []


# --- cancel proxy ---
def test_cancel_proxy_forwards_to_agent(http_server, monkeypatch):
    base = http_server("cancel")
    monkeypatch.setenv("AGENT_BASE_URL", base)
    result = asyncio.run(service.cancel_test_run("r9"))
    assert result["status_code"] == 200
    assert result["body"]["status"] == "success"
    assert result["body"]["run_id"] == "r9"


def test_cancel_proxy_respects_ssrf(monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:10123")
    with pytest.raises(HTTPException) as exc:
        service._resolve_allowed_target_url(AdminTestRunRequest(payload={}, target_agent_url="http://evil.example.com"))
    assert exc.value.status_code == 400
