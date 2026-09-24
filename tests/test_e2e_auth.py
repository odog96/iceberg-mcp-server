"""End-to-end: real FastMCP HTTP server + JWTVerifier built by identity.build_verifier(),
with a local JWKS standing in for Entra. Impala is mocked to record who each
connection would run as."""

import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

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
    mp.setenv("MCP_ENABLE_WHOAMI", "1")

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


async def test_whoami_tool_shows_what_the_server_verified(server_url, keys, connections):
    tok = token(keys, preferred_username="jgaragorry@corp.com", scp="access_as_user", appid="client-app-id")
    res = await call(server_url, tok, "whoami")
    out = json.loads(res.content[0].text)
    assert out["token_received"] is True
    assert out["issuer"] == ISSUER and out["audience"] == AUDIENCE
    assert out["requesting_app_id"] == "client-app-id"
    assert out["granted_scope"] == "access_as_user"
    assert out["mapped_cloudera_user"] == "jgaragorry"
    assert tok not in res.content[0].text  # the token itself is never returned


async def test_whoami_requires_a_valid_token(server_url, connections):
    with pytest.raises(Exception):
        await call(server_url, None, "whoami")


async def test_required_scope_is_enforced_when_configured(jwks_url, keys, monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ENTRA_JWKS_URI", jwks_url)
    monkeypatch.setenv("ENTRA_REQUIRED_SCOPE", "access_as_user")
    from iceberg_mcp_server import identity

    verifier = identity.build_verifier()
    assert await verifier.verify_token(token(keys, scp="access_as_user")) is not None
    assert await verifier.verify_token(token(keys, scp="something_else")) is None
    assert await verifier.verify_token(token(keys)) is None  # no scope at all


async def test_scope_not_enforced_by_default(jwks_url, keys, monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ENTRA_JWKS_URI", jwks_url)
    monkeypatch.delenv("ENTRA_REQUIRED_SCOPE", raising=False)
    from iceberg_mcp_server import identity

    assert await identity.build_verifier().verify_token(token(keys)) is not None


# --- scripts/curl_mcp.sh: the same server, checked with plain curl ---------------------------

CURL_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "curl_mcp.sh")
needs_curl = pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")


def run_curl_script(args, **env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("TOKEN", "TOKEN_FILE")}
    env.update(env_extra)
    return subprocess.run([CURL_SCRIPT, *args], env=env, capture_output=True, text=True, timeout=90)


@needs_curl
async def test_curl_script_lists_tools_with_a_valid_token(server_url, keys):
    out = run_curl_script([server_url, "list"], TOKEN=token(keys, scp="access_as_user"))
    assert out.returncode == 0, out.stderr
    for name in ("get_schema", "execute_query", "whoami"):
        assert name in out.stdout


@needs_curl
async def test_curl_script_calls_a_tool_and_shows_the_mapped_user(server_url, keys):
    tok = token(keys, preferred_username="jgaragorry@corp.com", scp="access_as_user")
    out = run_curl_script([server_url, "call", "whoami"], TOKEN=tok)
    assert out.returncode == 0, out.stderr
    assert "jgaragorry" in out.stdout
    assert tok not in out.stdout


@needs_curl
async def test_curl_script_reads_the_token_from_a_file(server_url, keys, tmp_path):
    f = tmp_path / "tok"
    f.write_text(token(keys, scp="access_as_user") + "\n")
    out = run_curl_script([server_url, "list"], TOKEN_FILE=str(f))
    assert out.returncode == 0, out.stderr


@needs_curl
async def test_curl_script_explains_a_401_without_a_token(server_url):
    out = run_curl_script([server_url, "list"])
    assert out.returncode == 1
    assert "HTTP 401" in out.stderr


@needs_curl
async def test_curl_script_health_needs_no_token(server_url):
    out = run_curl_script([server_url, "health"])
    assert out.returncode == 0 and "HTTP 200" in out.stdout
