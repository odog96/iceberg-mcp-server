import pytest

from iceberg_mcp_server import identity


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("ENTRA_TENANT_ID", "ENTRA_AUDIENCE", "ENTRA_USER_CLAIMS", "ALLOWED_USERS", "MCP_TEST_USER", "USER_MAP"):
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
