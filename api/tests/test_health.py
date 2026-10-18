"""API smoke test via httpx AsyncClient."""

import httpx
import pytest

from api.app.main import app
from mutascope import __version__


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_image_healthcheck_probe() -> None:
    """The Docker HEALTHCHECK's probe: 200 from /health is healthy, nothing listening is not."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from api.app.healthcheck import probe

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert probe(str(server.server_address[1]))
    finally:
        server.shutdown()
        server.server_close()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free = str(sock.getsockname()[1])
    assert not probe(free, timeout=1)
