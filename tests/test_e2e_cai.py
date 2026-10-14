"""End-to-end: real FastMCP HTTP server in CAI mode. The Remote-User header that CAI's proxy would
set is sent directly; Impala is mocked to record who each connection would run as."""

import socket
import threading
import time

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def connections():
    return []


@pytest.fixture(scope="module")
def server_url(connections):
    mp = pytest.MonkeyPatch()
    for k in ("ENTRA_TENANT_ID", "ENTRA_AUDIENCE", "MCP_TEST_USER", "ALLOWED_USERS", "USER_MAP"):
        mp.delenv(k, raising=False)
    mp.setenv("CAI_TRUSTED_CALLERS", "svc_chat")

    from iceberg_mcp_server import server
    from iceberg_mcp_server.tools import impala_tools

    class Cur:
        description = [("x",)]

        def execute(self, q):
            pass

        def fetchall(self):
            return [("t1",)]

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            pass

    def fake_connect(effective_user):
        connections.append(effective_user)
        return Conn()

    mp.setattr(impala_tools, "get_db_connection", fake_connect)

    import importlib

    server = importlib.reload(server)
    port = free_port()
    uv = uvicorn.Server(uvicorn.Config(server.mcp.http_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=uv.run, daemon=True).start()
    while not uv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    uv.should_exit = True
    mp.undo()


async def call(url, headers):
    async with Client(StreamableHttpTransport(url, headers=headers)) as c:
        return await c.call_tool("get_schema", {}, raise_on_error=False)


async def test_trusted_caller_runs_as_end_user(server_url, connections):
    connections.clear()
    res = await call(server_url, {"Remote-User": "svc_chat", "X-On-Behalf-Of": "mschuler"})
    assert not res.is_error
    assert connections == ["mschuler"]


async def test_direct_caller_runs_as_itself(server_url, connections):
    connections.clear()
    res = await call(server_url, {"Remote-User": "pauldefusco"})
    assert not res.is_error
    assert connections == ["pauldefusco"]


async def test_untrusted_caller_cannot_act_on_behalf(server_url, connections):
    connections.clear()
    res = await call(server_url, {"Remote-User": "mallory", "X-On-Behalf-Of": "ozarate"})
    assert res.is_error
    assert connections == []


async def test_no_remote_user_rejected(server_url, connections):
    connections.clear()
    res = await call(server_url, {"X-On-Behalf-Of": "ozarate"})
    assert res.is_error
    assert connections == []
