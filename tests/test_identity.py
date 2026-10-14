import pytest

from iceberg_mcp_server import identity


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("ENTRA_TENANT_ID", "ENTRA_AUDIENCE", "ENTRA_USER_CLAIMS", "ALLOWED_USERS", "MCP_TEST_USER", "USER_MAP", "CAI_TRUSTED_CALLERS"):
        monkeypatch.delenv(k, raising=False)


def test_local_part_of_upn():
    assert identity.map_claims_to_user({"preferred_username": "OZarate@corp.com"}) == "ozarate"


def test_claim_fallback_order():
    assert identity.map_claims_to_user({"upn": "a@x.com", "email": "b@x.com"}) == "a"


def test_no_claim_rejected():
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"sub": "abc", "oid": "123"})


@pytest.mark.parametrize("bad", ["a&doAs=root@x.com", "with space@x.com", "@x.com", "1abc@x.com"])
def test_unmappable_identity_rejected(bad):
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"preferred_username": bad})


def test_allowlist(monkeypatch):
    monkeypatch.setenv("ALLOWED_USERS", "ozarate, jgaragorry")
    assert identity.map_claims_to_user({"upn": "jgaragorry@x.com"}) == "jgaragorry"
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"upn": "mallory@x.com"})


def test_configured_claims(monkeypatch):
    monkeypatch.setenv("ENTRA_USER_CLAIMS", "email")
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"preferred_username": "a@x.com"})


def test_no_token_denied_by_default():
    with pytest.raises(identity.IdentityError):
        identity.resolve_effective_user(None)


def test_test_user_only_without_entra(monkeypatch):
    monkeypatch.setenv("MCP_TEST_USER", "ozarate")
    assert identity.resolve_effective_user(None) == "ozarate"
    monkeypatch.setenv("ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("ENTRA_AUDIENCE", "a")
    with pytest.raises(identity.IdentityError):
        identity.resolve_effective_user(None)


def test_build_verifier_none_when_unconfigured():
    assert identity.build_verifier() is None


def test_user_map_maps_odd_identity(monkeypatch):
    monkeypatch.setenv("USER_MAP", "oliver_gmail.com#EXT#@t.onmicrosoft.com=ozarate, other@corp.com=JCaseiro")
    assert identity.map_claims_to_user({"preferred_username": "Oliver_Gmail.com#EXT#@t.onmicrosoft.com"}) == "ozarate"
    assert identity.map_claims_to_user({"upn": "other@corp.com"}) == "jcaseiro"


def test_user_map_is_exclusive_no_local_part_fallback(monkeypatch):
    monkeypatch.setenv("USER_MAP", "me@corp.com=ozarate")
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"preferred_username": "fcobo@corp.com"})


def test_user_map_value_still_validated(monkeypatch):
    monkeypatch.setenv("USER_MAP", "me@corp.com=a&doAs=root")
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"preferred_username": "me@corp.com"})


def test_user_map_respects_allowlist(monkeypatch):
    monkeypatch.setenv("USER_MAP", "me@corp.com=ozarate")
    monkeypatch.setenv("ALLOWED_USERS", "jcaseiro")
    with pytest.raises(identity.IdentityError):
        identity.map_claims_to_user({"preferred_username": "me@corp.com"})


# --- whoami / describe_caller -------------------------------------------------

import time
from types import SimpleNamespace


def _fake_token(**extra):
    now = int(time.time())
    claims = {
        "iss": "https://sts.windows.net/t/", "aud": "api://x", "appid": "client-app", "ver": "1.0",
        "scp": "access_as_user", "amr": ["pwd", "mfa"], "iat": now, "exp": now + 3600,
        "email": "me@corp.com",
        # things that must NOT be echoed back
        "oid": "OBJECT-ID", "sub": "SUBJECT", "ipaddr": "1.2.3.4", "aio": "OPAQUE", "sid": "SESSION",
    }
    claims.update(extra)
    return SimpleNamespace(claims=claims, token="RAW.TOKEN.VALUE")


def test_whoami_enabled_flag(monkeypatch):
    assert identity.whoami_enabled() is False
    monkeypatch.setenv("MCP_ENABLE_WHOAMI", "1")
    assert identity.whoami_enabled() is True


def test_describe_caller_shows_verified_details(monkeypatch):
    monkeypatch.setenv("USER_MAP", "me@corp.com=ozarate")
    out = identity.describe_caller(_fake_token())
    assert out["token_received"] is True
    assert out["issuer"] == "https://sts.windows.net/t/"
    assert out["audience"] == "api://x"
    assert out["requesting_app_id"] == "client-app"
    assert out["granted_scope"] == "access_as_user"
    assert out["identity_in_token"] == {"email": "me@corp.com"}
    assert out["mapped_cloudera_user"] == "ozarate"
    assert 58 <= out["minutes_until_expiry"] <= 60
    assert out["raw_token_included"] is False


def test_describe_caller_never_leaks_secrets_or_extra_identifiers(monkeypatch):
    monkeypatch.setenv("USER_MAP", "me@corp.com=ozarate")
    dumped = str(identity.describe_caller(_fake_token()))
    for leaked in ("RAW.TOKEN.VALUE", "OBJECT-ID", "SUBJECT", "1.2.3.4", "OPAQUE", "SESSION"):
        assert leaked not in dumped


def test_describe_caller_reports_unmapped_user(monkeypatch):
    monkeypatch.setenv("USER_MAP", "someone@else.com=ozarate")
    out = identity.describe_caller(_fake_token())
    assert out["mapped_cloudera_user"] is None
    assert "not permitted" in out["mapping_result"]


def test_describe_caller_without_token_is_test_mode_or_unauthenticated(monkeypatch):
    assert identity.describe_caller(None)["cloudera_user"] is None
    monkeypatch.setenv("MCP_TEST_USER", "ozarate")
    out = identity.describe_caller(None)
    assert out["token_received"] is False and out["cloudera_user"] == "ozarate"


# --- CAI mode -------------------------------------------------------------------


@pytest.fixture
def cai(monkeypatch):
    monkeypatch.setenv("CAI_TRUSTED_CALLERS", "svc_chat")


def test_cai_caller_runs_as_itself(cai):
    assert identity.resolve_effective_user(None, {"remote-user": "mschuler"}) == "mschuler"


def test_cai_trusted_caller_acts_on_behalf(cai):
    headers = {"remote-user": "svc_chat", "x-on-behalf-of": "PaulDefusco"}
    assert identity.resolve_effective_user(None, headers) == "pauldefusco"


def test_cai_untrusted_caller_cannot_act_on_behalf(cai):
    with pytest.raises(identity.IdentityError, match="on behalf"):
        identity.resolve_effective_user(None, {"remote-user": "mallory", "x-on-behalf-of": "ozarate"})


def test_cai_on_behalf_of_self_is_fine_for_anyone(cai):
    assert identity.resolve_effective_user(None, {"remote-user": "mallory", "x-on-behalf-of": "mallory"}) == "mallory"


def test_cai_missing_remote_user_rejected(cai):
    with pytest.raises(identity.IdentityError, match="authentication turned on"):
        identity.resolve_effective_user(None, {"x-on-behalf-of": "ozarate"})


@pytest.mark.parametrize("bad", ["a&doAs=root", "with space", "1abc"])
def test_cai_on_behalf_user_validated(cai, bad):
    with pytest.raises(identity.IdentityError):
        identity.resolve_effective_user(None, {"remote-user": "svc_chat", "x-on-behalf-of": bad})


def test_cai_respects_allowlist(cai, monkeypatch):
    monkeypatch.setenv("ALLOWED_USERS", "ozarate")
    with pytest.raises(identity.IdentityError, match="not permitted"):
        identity.resolve_effective_user(None, {"remote-user": "svc_chat", "x-on-behalf-of": "mschuler"})


def test_cai_mode_disables_test_user(cai, monkeypatch):
    monkeypatch.setenv("MCP_TEST_USER", "ozarate")
    with pytest.raises(identity.IdentityError):
        identity.resolve_effective_user(None, {})


def test_cai_mode_ignored_when_entra_configured(cai, monkeypatch):
    # main() refuses to start with both; resolution must still never fall back to headers.
    monkeypatch.setenv("ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("ENTRA_AUDIENCE", "a")
    with pytest.raises(identity.IdentityError):
        identity.resolve_effective_user(None, {"remote-user": "ozarate"})


def test_describe_cai_caller(cai):
    out = identity.describe_cai_caller({"remote-user": "svc_chat", "x-on-behalf-of": "ozarate"})
    assert out["mode"] == "cai" and out["caller_is_trusted"] is True and out["cloudera_user"] == "ozarate"
