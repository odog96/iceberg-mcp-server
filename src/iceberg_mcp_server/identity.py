"""Entra token validation setup and token-identity -> Cloudera username mapping.

The MCP server is the only gate for the Azure client (the CML Application allows
unauthenticated access), so everything here denies by default.

Environment variables:
    ENTRA_TENANT_ID        tenant GUID (required for auth)
    ENTRA_AUDIENCE         token audience: the app registration's Application ID URI
                           (api://<app-id>) or, for v2 tokens, its client id GUID
    ENTRA_ISSUER           optional; default is the v2.0 issuer. For v1 tokens use
                           https://sts.windows.net/<tenant-id>/
    ENTRA_JWKS_URI         optional; default is the tenant's v2.0 keys endpoint
    ENTRA_USER_CLAIMS      comma-separated claim names tried in order
                           (default: preferred_username,upn,email)
    USER_MAP               optional comma-separated identity=cloudera_user pairs, e.g.
                           me@corp.com=ozarate,other@corp.com=jcaseiro. When set it is
                           exclusive: an identity not listed is rejected (no local-part
                           fallback). Use it when token identities are not Cloudera names.
    ALLOWED_USERS          optional comma-separated Cloudera usernames; if set, any
                           other mapped user is rejected
    MCP_TEST_USER          fixed Cloudera user for test deployments with no token
                           validation. Only honoured when ENTRA_* auth is NOT
                           configured; startup logs a loud warning.
"""

import os

from fastmcp.server.auth.providers.jwt import JWTVerifier

from iceberg_mcp_server.tools.impala_tools import validate_username

DEFAULT_USER_CLAIMS = ("preferred_username", "upn", "email")


class IdentityError(Exception):
    """The caller could not be mapped to an allowed Cloudera user."""


def _csv(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


def auth_configured() -> bool:
    return bool(os.getenv("ENTRA_TENANT_ID") and os.getenv("ENTRA_AUDIENCE"))


def build_verifier():
    """Return a JWTVerifier for Entra tokens, or None if ENTRA_* is not configured."""
    if not auth_configured():
        return None
    tenant = os.environ["ENTRA_TENANT_ID"]
    return JWTVerifier(
        jwks_uri=os.getenv("ENTRA_JWKS_URI") or f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        issuer=os.getenv("ENTRA_ISSUER") or f"https://login.microsoftonline.com/{tenant}/v2.0",
        audience=os.environ["ENTRA_AUDIENCE"],
        algorithm="RS256",
    )


def _user_map() -> dict[str, str]:
    mapping = {}
    for pair in _csv("USER_MAP"):
        identity, sep, user = pair.partition("=")
        if sep and identity.strip() and user.strip():
            mapping[identity.strip().lower()] = user.strip().lower()
    return mapping


def map_claims_to_user(claims: dict) -> str:
    """Map validated token claims to a Cloudera username, or raise IdentityError.

    With USER_MAP set, the first identity claim value found in the map wins and anything else
    is rejected. Without it, uses the local part of the first present identity claim
    (ozarate@corp.com -> ozarate).
    """
    claim_names = _csv("ENTRA_USER_CLAIMS") or list(DEFAULT_USER_CLAIMS)
    candidates = [claims[c].strip() for c in claim_names if isinstance(claims.get(c), str) and claims[c].strip()]
    if not candidates:
        raise IdentityError("Token has no usable identity claim")

    mapping = _user_map()
    if mapping:
        user = next((mapping[c.lower()] for c in candidates if c.lower() in mapping), None)
        if user is None:
            raise IdentityError("User is not permitted")
    else:
        user = candidates[0].split("@", 1)[0].strip().lower()

    try:
        validate_username(user)
    except ValueError:
        raise IdentityError("Token identity does not map to a valid Cloudera username") from None

    allowed = [u.lower() for u in _csv("ALLOWED_USERS")]
    if allowed and user not in allowed:
        raise IdentityError("User is not permitted")
    return user


def resolve_effective_user(access_token) -> str:
    """Effective Cloudera user for the current request."""
    if access_token is not None:
        return map_claims_to_user(access_token.claims)
    # No validated token. Only allowed in explicit test mode, never when Entra auth is on.
    test_user = os.getenv("MCP_TEST_USER")
    if test_user and not auth_configured():
        try:
            return validate_username(test_user)
        except ValueError:
            raise IdentityError("MCP_TEST_USER is not a valid username") from None
    raise IdentityError("Authentication required")
