"""check_cai_application_auth: CAI mode refuses to start when its Application allows unauthenticated access."""

import json
import sys
from types import SimpleNamespace

import pytest

from iceberg_mcp_server import server

MY_ENV = {"CAI_TRUSTED_CALLERS": "ozarate", "IMPALA_DATABASE": "mcp_demo"}


def app(bypass, env=MY_ENV, script="iceberg-mcp-server/start_mcp.py", name="mcp"):
    return SimpleNamespace(name=name, script=script, bypass_authentication=bypass, environment=json.dumps(env))


@pytest.fixture
def cai_apps(monkeypatch):
    for k, v in MY_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CDSW_PROJECT_ID", "p1")

    def install(apps):
        client = SimpleNamespace(list_applications=lambda pid, page_size: SimpleNamespace(applications=apps))
        monkeypatch.setitem(sys.modules, "cmlapi", SimpleNamespace(default_client=lambda: client))

    return install


def test_auth_on_passes(cai_apps):
    cai_apps([app(bypass=False)])
    assert server.check_cai_application_auth() is None


def test_auth_off_refuses(cai_apps):
    cai_apps([app(bypass=True)])
    assert "allows unauthenticated access" in server.check_cai_application_auth()


def test_other_apps_ignored(cai_apps):
    cai_apps(
        [
            app(bypass=False),
            app(bypass=True, script="start_chat.py", name="chat"),
            app(bypass=True, env={"MCP_TEST_USER": "ozarate"}, name="test-mode mcp"),
            app(bypass=True, env={**MY_ENV, "IMPALA_DATABASE": "other"}, name="other cai mcp"),
        ]
    )
    assert server.check_cai_application_auth() is None


def test_ambiguous_only_warns(cai_apps, capsys):
    cai_apps([app(bypass=False), app(bypass=True, name="twin")])
    assert server.check_cai_application_auth() is None
    assert "could not identify" in capsys.readouterr().out


def test_api_unavailable_only_warns(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "cmlapi", None)
    assert server.check_cai_application_auth() is None
    assert "could not check" in capsys.readouterr().out
