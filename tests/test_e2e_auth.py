"""End-to-end: real FastMCP HTTP server + JWTVerifier built by identity.build_verifier(),
with a local JWKS standing in for Entra. Impala is mocked to record who each
connection would run as."""

import http.server
import json
import socket
import threading
import time

import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from jwt.algorithms import RSAAlgorithm

TENANT = "11111111-2222-3333-4444-555555555555"
AUDIENCE = "api://mcp-test-app"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048), rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )


@pytest.fixture(scope="module")
def jwks_url(keys):
    jwk = json.loads(RSAAlgorithm.to_jwk(keys[0].public_key()))
    jwk.update(kid="test-kid", use="sig", alg="RS256")
    body = json.dumps({"keys": [jwk]}).encode()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    port = free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/keys"
    srv.shutdown()


@pytest.fixture(scope="module")
def connections():
    return []


@pytest.fixture(scope="module")
def server_url(jwks_url, connections):
    mp = pytest.MonkeyPatch()
    mp.setenv("ENTRA_TENANT_ID", TENANT)
    mp.setenv("ENTRA_AUDIENCE", AUDIENCE)
    mp.setenv("ENTRA_JWKS_URI", jwks_url)
    mp.setenv("ALLOWED_USERS", "ozarate,jgaragorry")

    from iceberg_mcp_server import server
    from iceberg_mcp_server.tools import impala_tools

    class Cur:
        description = [("x",)]

        def execute(self, q):
            self.q = q

        def fetchall(self):
            return [("t1",), ("t2",)]

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

    # server module built its auth at import time, possibly before env was set
    import importlib

    server = importlib.reload(server)
    port = free_port()
    config = uvicorn.Config(server.mcp.http_app(), host="127.0.0.1", port=port, log_level="error")
    uv = uvicorn.Server(config)
    threading.Thread(target=uv.run, daemon=True).start()
    while not uv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    uv.should_exit = True
    mp.undo()


def token(keys, key_idx=0, **overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
        "preferred_username": "OZarate@corp.com",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, keys[key_idx], algorithm="RS256", headers={"kid": "test-kid"})


async def call(url, tok, tool="get_schema", args=None):
    async with Client(url, auth=BearerAuth(tok) if tok else None) as c:
        return await c.call_tool(tool, args or {})


async def test_valid_token_runs_as_mapped_user(server_url, keys, connections):
    connections.clear()
    res = await call(server_url, token(keys))
    assert json.loads(res.content[0].text) == ["t1", "t2"]
    assert connections == ["ozarate"]


async def test_execute_query_uses_token_user(server_url, keys, connections):
    connections.clear()
    tok = token(keys, preferred_username="jgaragorry@corp.com")
    await call(server_url, tok, "execute_query", {"query": "select 1"})
    assert connections == ["jgaragorry"]


@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("expired", {"exp": int(time.time()) - 60}),
        ("wrong audience", {"aud": "api://someone-else"}),
        ("wrong issuer", {"iss": "https://evil.example.com/"}),
    ],
)
async def test_invalid_tokens_rejected(server_url, keys, connections, label, kwargs):
    connections.clear()
    with pytest.raises(Exception):
        await call(server_url, token(keys, **kwargs))
    assert connections == [], label


async def test_wrong_signing_key_rejected(server_url, keys, connections):
    connections.clear()
    with pytest.raises(Exception):
        await call(server_url, token(keys, key_idx=1))
    assert connections == []


async def test_no_token_rejected(server_url, connections):
    connections.clear()
    with pytest.raises(Exception):
        await call(server_url, None)
    assert connections == []


async def test_unmapped_user_rejected(server_url, keys, connections):
    connections.clear()
    with pytest.raises(Exception):
        await call(server_url, token(keys, preferred_username="mallory@corp.com"))
    assert connections == []


async def test_token_without_identity_claim_rejected(server_url, keys, connections):
    connections.clear()
    with pytest.raises(Exception):
        await call(server_url, token(keys, preferred_username=None, sub="abc"))
    assert connections == []


async def test_write_query_never_connects(server_url, keys, connections):
    connections.clear()
    res = await call(server_url, token(keys), "execute_query", {"query": "drop table t"})
    assert "read-only" in res.content[0].text
    assert connections == []


@pytest.mark.parametrize("path", ["/", "/health"])
async def test_landing_routes_are_public_and_static(server_url, connections, path):
    import httpx

    connections.clear()
    r = httpx.get(server_url.removesuffix("/mcp") + path)
    assert r.status_code == 200
    assert "/mcp" in r.text
    assert connections == []
