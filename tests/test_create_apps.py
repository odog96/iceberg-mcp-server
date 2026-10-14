"""scripts/create_mcp_app.py: which applications each mode creates, and with which login setting."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from create_mcp_app import plan_apps  # noqa: E402

BASE = {
    "IMPALA_HOST": "https://coord.example.com/",
    "IMPALA_USER": "srv_machine",
    "IMPALA_PASSWORD_B64": "cGFzcyQ=",
}


def plan(mode, **extra):
    return plan_apps(mode, {**BASE, **extra}, "repo/", "ws.example.com", "iceberg-mcp")


def test_cai_mode_creates_both_apps_with_login_on():
    apps, notes = plan("cai", CAI_TRUSTED_CALLERS="ozarate", CAI_INFERENCE_BASE_URL="http://llm/v1")
    mcp, chat = apps
    assert mcp["bypass_authentication"] is False and chat["bypass_authentication"] is False
    assert mcp["script"] == "repo/start_mcp.py" and chat["script"] == "repo/start_chat.py"
    assert mcp["environment"]["CAI_TRUSTED_CALLERS"] == "ozarate"
    assert mcp["environment"]["IMPALA_HOST"] == "coord.example.com"
    assert chat["subdomain"] == "iceberg-mcp-chat"
    assert chat["environment"]["MCP_URL"] == "https://iceberg-mcp.ws.example.com/mcp"
    assert chat["environment"]["CAI_INFERENCE_BASE_URL"] == "http://llm/v1"
    assert not [k for k in chat["environment"] if k.startswith("IMPALA_")]  # not copied into the chat app


def test_cai_mode_without_model_skips_chat_and_says_why():
    apps, notes = plan("cai", CAI_TRUSTED_CALLERS="ozarate")
    assert len(apps) == 1
    assert any("Chat app not created" in n for n in notes)


@pytest.mark.parametrize(
    "mode,extra", [("entra", {"ENTRA_TENANT_ID": "t", "ENTRA_AUDIENCE": "api://x"}), ("test", {"MCP_TEST_USER": "u"})]
)
def test_entra_and_test_modes_are_mcp_only_with_login_off(mode, extra):
    apps, _ = plan(mode, **extra)
    assert len(apps) == 1 and apps[0]["bypass_authentication"] is True
    assert {k: apps[0]["environment"][k] for k in extra} == extra


def test_entra_optional_settings_are_copied():
    apps, _ = plan("entra", ENTRA_TENANT_ID="t", ENTRA_AUDIENCE="a", ENTRA_ISSUER="https://sts/", USER_MAP="a=b")
    assert apps[0]["environment"]["ENTRA_ISSUER"] == "https://sts/" and apps[0]["environment"]["USER_MAP"] == "a=b"


@pytest.mark.parametrize(
    "mode,extra,missing",
    [
        ("cai", {}, "CAI_TRUSTED_CALLERS"),
        ("entra", {"ENTRA_TENANT_ID": "t"}, "ENTRA_AUDIENCE"),
        ("test", {}, "MCP_TEST_USER"),
    ],
)
def test_missing_mode_settings_are_named(mode, extra, missing):
    with pytest.raises(ValueError, match=missing):
        plan(mode, **extra)


@pytest.mark.parametrize(
    "mode,extra",
    [
        # Applications can see project-level variables, so these would switch the server's mode.
        ("test", {"MCP_TEST_USER": "u", "ENTRA_TENANT_ID": "t"}),
        ("test", {"MCP_TEST_USER": "u", "CAI_TRUSTED_CALLERS": "x"}),
        ("cai", {"CAI_TRUSTED_CALLERS": "x", "ENTRA_AUDIENCE": "a"}),
        ("entra", {"ENTRA_TENANT_ID": "t", "ENTRA_AUDIENCE": "a", "CAI_TRUSTED_CALLERS": "x"}),
    ],
)
def test_another_modes_settings_are_refused(mode, extra):
    with pytest.raises(ValueError, match="another mode"):
        plan(mode, **extra)


def test_test_user_left_over_in_cai_mode_is_only_a_note():
    _, notes = plan("cai", CAI_TRUSTED_CALLERS="x", MCP_TEST_USER="u")
    assert any("MCP_TEST_USER is ignored" in n for n in notes)


def test_missing_connection_settings_are_named():
    with pytest.raises(ValueError, match="IMPALA_PASSWORD_B64"):
        plan_apps("test", {"IMPALA_HOST": "h", "IMPALA_USER": "u", "MCP_TEST_USER": "x"}, "", "d", "s")


def test_unknown_mode():
    with pytest.raises(ValueError, match="MCP_MODE"):
        plan("prod")
