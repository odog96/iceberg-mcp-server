# JWT auth for Impala — plan and next steps

## Context

This POC lets ~10 users in an Azure-hosted chatbot query Cloudera data (via
Impala) through this MCP server, which runs in a separate AWS account. The
end goal is per-user attribution: each Azure identity should map to a
distinct Cloudera identity, ideally authenticated through one IdP hop rather
than a shared LDAP username/password baked into the MCP server's environment
(the current state — see `impala_tools.py`).

We're moving off LDAP auth toward **JWT bearer auth** at the Impala
coordinator, since it's the mechanism that can plausibly carry a real user
identity end-to-end instead of a single shared service credential. We are
also mid-migration to a new Cloudera tenant, so none of this is configured
yet.

## Decision so far

- Auth mechanism for Impala: **JWT**, not LDAP.
- Client library: **impyla (already pinned in `pyproject.toml`, 0.22.0)
  supports this natively** — confirmed by reading the installed package:
  - `impala.dbapi.connect(auth_mechanism='JWT', jwt=<token>, use_http_transport=True, use_ssl=True, ...)`
  - Requires HTTP transport (`use_http_transport=True`); JWT auth is not
    supported over the binary/Thrift transport.
  - The token is sent as `Authorization: Bearer <jwt>` on the HTTP
    connection (`impala/_thrift_api.py`).
  - No code change needed in `impala_tools.py` to *support* JWT — only to
    plumb a real per-request token into `connect()` instead of the current
    static env-var username/password.

## Confirmed

- The virtual warehouse UI has a "generate token" action, so we'll have a
  way to mint a test JWT manually (outside of any Azure/Entra flow) to
  prove out the MCP server's connection code before the identity-federation
  piece is solved.

## Open questions (need the new tenant + VW to answer)

1. **Which JWT does the coordinator actually trust?**
   When you enable "JWT token generation" on the new virtual warehouse,
   confirm whether the coordinator validates:
   - a CDP Control Plane–issued JWT (minted after the Control Plane itself
     federates with an enterprise IdP via SAML/OIDC), or
   - an externally-issued JWT validated directly against a configured
     issuer/JWKS URL (which could point at Azure Entra ID's own JWKS).

   This determines whether "one IdP, one hop" (Entra ID token accepted by
   Impala directly) is achievable, or whether there's a required
   intermediate exchange (Entra ID → CDP Control Plane SSO → CDP-issued
   JWT → Impala).

2. **How is the issuer/JWKS configured on the coordinator?**
   Once the VW is up, get the exact config (issuer URL, JWKS URL/audience,
   any claim-mapping) so we know what a valid token has to look like and
   where it comes from.

3. **Per-user Impala identity vs. per-user Cloudera user record.**
   Confirm whether a validated JWT's subject claim needs to correspond to
   an actual Cloudera user (`user001` etc.) that already exists in
   Cloudera's user list, or whether that's handled separately (e.g. via
   proxy-user impersonation on top of a single JWT-authenticated service
   identity — still an open design choice from earlier discussion, not
   required to resolve before the VW/tenant work below).

## Next steps

### Cloudera side (blocked on you / new tenant)
- [ ] Stand up the new virtual warehouse in the new tenant.
- [ ] Enable JWT token generation/auth on the coordinator.
- [ ] Record: coordinator host, port, issuer, JWKS URL/audience, and how to
      mint a test token (e.g. via `cdp` CLI, CDP Control Plane UI, or
      whatever the new tenant's flow is).
- [ ] Update `impala_details.txt` (gitignored — confirm it's still ignored
      after the tenant switch) with the new coordinator host and JWT
      details, replacing the LDAP username/password.

### MCP server side (can start once we have a test token)
- [ ] Add a `get_db_connection()` path in
      `src/iceberg_mcp_server/tools/impala_tools.py` that uses
      `auth_mechanism='JWT'` + `jwt=<token>` instead of `LDAP` +
      `user`/`password`.
- [ ] Decide where the JWT comes from per-request: today `get_db_connection()`
      reads static env vars; a real per-user JWT has to come from the
      inbound MCP request (i.e. the chatbot's Entra ID token, forwarded
      through the MCP server), not a server-side env var. This needs the
      MCP server's HTTP transport to accept and forward a bearer token per
      call rather than authenticating once at process startup.
- [ ] Validate the incoming token's signature/claims against Entra ID's
      JWKS at the MCP server *before* using it against Impala, so the MCP
      server itself enforces "who is allowed to call at all" independently
      of whatever Impala decides to trust.
- [ ] Prove out a single manual connection first (hardcoded test token,
      similar to the LDAP connection test we already ran) before wiring it
      into the FastMCP tool layer.

### Deferred (explicitly not blocking the above)
- Per-user proxy impersonation at the Impala layer (`authorized_proxy_user_config`)
  for cases where the JWT subject itself isn't a first-class Cloudera user.
- CML Application deployment fixes (`start_mcp.py`'s `PROJECT_DIR` mismatch,
  `IMPALA_PASSWORD_B64` handling) — still valid but orthogonal to the auth
  mechanism change.
