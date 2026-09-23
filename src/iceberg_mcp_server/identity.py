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
    ENTRA_REQUIRED_SCOPE   optional; if set (e.g. access_as_user), tokens whose scope claim does
                           not include it are rejected. Leave unset until `whoami` has shown
                           what the real caller's token contains.
    MCP_ENABLE_WHOAMI      optional; set to 1 to expose a `whoami` diagnostic tool that shows
                           non-secret details of the caller's verified token (never the token)
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
import time
from datetime import datetime, timezone

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
    required_scope = os.getenv("ENTRA_REQUIRED_SCOPE", "").strip()
    return JWTVerifier(
        jwks_uri=os.getenv("ENTRA_JWKS_URI") or f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        issuer=os.getenv("ENTRA_ISSUER") or f"https://login.microsoftonline.com/{tenant}/v2.0",
        audience=os.environ["ENTRA_AUDIENCE"],
        algorithm="RS256",
        required_scopes=[required_scope] if required_scope else None,
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


def whoami_enabled() -> bool:
    return os.getenv("MCP_ENABLE_WHOAMI", "").strip().lower() in ("1", "true", "yes")


def _iso_utc(ts) -> str | None:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
    return None


def describe_caller(access_token) -> dict:
    """Non-secret summary of the verified token behind the current request.

    Never includes the token itself or its signature, and leaves out identifiers that are not
    needed to explain the flow (object id, subject, IP address, session ids).
    """
    if access_token is None:
        try:
            return {"token_received": False, "cloudera_user": resolve_effective_user(None),
                    "note": "No token: running as the fixed MCP_TEST_USER (test mode)."}
        except IdentityError as e:
            return {"token_received": False, "cloudera_user": None, "note": str(e)}

    claims = access_token.claims or {}
    try:
        cloudera_user, mapping_note = map_claims_to_user(claims), "mapped"
    except IdentityError as e:
        cloudera_user, mapping_note = None, str(e)

    exp = claims.get("exp")
    return {
        "token_received": True,
        "checks_passed_before_this_ran": [
            "signature (Microsoft's public signing keys)",
            "issuer (our tenant)",
            "audience (our API)",
            "not expired",
        ],
        "issuer": claims.get("iss"),
        "audience": claims.get("aud"),
        "requesting_app_id": claims.get("appid") or claims.get("azp"),
        "token_version": claims.get("ver"),
        "granted_scope": claims.get("scp"),
        "sign_in_methods": claims.get("amr"),
        "issued_at_utc": _iso_utc(claims.get("iat")),
        "expires_at_utc": _iso_utc(exp),
        "minutes_until_expiry": round((exp - time.time()) / 60) if isinstance(exp, (int, float)) else None,
        "identity_in_token": {n: claims[n] for n in ("preferred_username", "upn", "email") if isinstance(claims.get(n), str)},
        "mapped_cloudera_user": cloudera_user,
        "mapping_result": mapping_note,
        "raw_token_included": False,
    }
