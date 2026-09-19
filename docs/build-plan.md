# Build-out plan: Azure chatbot -> Cloudera MCP server -> Impala (per-user audit)

Goal of this file: someone (or a fresh Claude session) can open a new Cloudera
environment and start building with zero ramp-up. Everything below was
reconstructed from Oliver's notes after prior work was lost. Facts marked
**(unverified)** come from those notes or from Claude's general knowledge and
have not been re-tested in the new environment.

**Working rule for the rebuild: commit after every completed step.** The last
attempt lost work because it lived in a shared environment and was never
committed.

## 1. Customer requirement (Sofra POC)

Sofra's AI team runs an Azure chatbot and currently copies data out of
Cloudera into Azure. They want the chatbot to query data in Cloudera directly.

Hard requirement: **governance and auditability with identity mapping.** If
"User 123" asks the chatbot a question, the Cloudera audit logs must show
"User 123" querying that specific table, not a shared service account.

Original ~10 Azure users map 1:1 to permitted Cloudera users.

## 2. Architecture

See `chat_w_ur_data_Arch.png` (repo root).

```
Azure client (Chat App + LLM)
   |  Entra ID token (Bearer)
   v
MCP server  (CML Application, inside Cloudera)
   |  1. validate the Entra token (signature, issuer, audience, expiry)
   |  2. map token identity -> Cloudera username
   |  3. connect to Impala as the machine user, impersonating that username
   v
Impala Virtual Warehouse (CDW)  ->  Ranger + audit logs show the end user
```

Key design decisions:

- **Impala-side JWT auth is OFF.** An earlier plan had Impala validate a JWT
  directly; Oliver hit several problems with it and moved on. (Specific
  problems were not recorded; add them here when remembered.) The Entra token
  is validated only by the MCP server, in code.
- **Impala connection = LDAP with a trusted machine user + `doAs`.** The machine
  user authenticates with its workload password; the connection asks Impala to
  run as the end user via `http_path='cliservice?doAs=<user>'`. Impala's
  `authorized_proxy_user_config` decides who the machine user may impersonate,
  so it is a second line of defence behind the MCP server's own checks.
- **The CML Application allows unauthenticated access.** The Azure client is
  external and cannot use CML auth, so the Entra token check in the MCP
  server's code is the only gate. It must deny by default.
- **Email-to-username mapping.** Start with the email/UPN local part
  (`ozarate@corp.com` -> `ozarate`). Alternative if that is not reliable: a
  mapping table stored in Cloudera, looked up (by the machine user) after the
  token is validated.

## 3. Status at time of writing

| Piece | State |
|---|---|
| Python script: machine user impersonating a user via `doAs` | Built and worked before; **lost**, must be rewritten |
| `doAs` logic ported into MCP server (`impala_tools.py`) | Built and worked before; **lost** |
| Azure token issuance + validation in the MCP server | **Never built** |
| Azure account access, Azure AI Foundry model access (IT approval) | Pending, outside our control |
| Machine user / VW in current sandbox | Not possible: no admin rights. Must use an environment where a machine user can be created |

Repo state: forked from `cloudera/iceberg-mcp-server`, with two CML commits
(port binding, password injection). Tools: `execute_query`, `get_schema`.
Connection settings come from env vars in
`src/iceberg_mcp_server/tools/impala_tools.py`.

## 4. Before you start: bring these

- [ ] An environment where you have CDP admin rights (to create a machine user)
- [ ] Machine user name (previously `srv_srvc_mcp_proxy`) and its **workload
      password** (the workload password is per-user, not per-environment)
- [ ] Names of the users to impersonate (previously `ozarate`, `jgaragorry`, `fcobo`)
- [ ] Entra tenant ID, and the app registration that represents the MCP server
      (its Application ID URI is the token audience)
- [ ] Put connection details in `impala_details.txt` (already gitignored). Never
      commit passwords.

## 5. Track A: Cloudera setup (outside this repo)

Runbook from Oliver's notes. **(unverified in new env)**

**Phase 1: identity (CDP Control Plane)**
1. Create the machine user if it does not exist (needs admin).
2. In the target Environment, grant the machine user **EnvironmentUser** and
   **DWUser**.
3. Environment > Actions > **Sync Users**. Wait for completion so FreeIPA has
   the account.
4. Connection username is the machine user name (`srv_srvc_mcp_proxy`) plus
   its workload password.

**Phase 2: Virtual Warehouse (CDW)**
1. Create a new VW in the environment.
2. Type must be **Impala** (the console defaults to Hive).
3. **Enable JWT Authentication = off.**

**Phase 3: trusted proxy config (CDW)**
1. VW > Configurations > Impala Coordinator.
2. Set the top dropdown to **flagfile**.
3. Add Custom Configuration:
   - Key: `authorized_proxy_user_config`
   - Value: `<env system account>=*;srv_srvc_mcp_proxy=ozarate,jgaragorry,fcobo`
   - The system account was `srv_env-48wgkh` in the old environment. It is
     environment-specific, so **read the existing value first** and append your
     machine-user rule rather than overwriting it.
4. Save and let the VW restart.

**Phase 4: data access**
- Each impersonated user needs Ranger permission on the tables you will query.
  Otherwise `doAs` connects fine but returns nothing or errors on access.

**Gate for Track A:** the coordinator host is known and the machine user can
open a plain LDAP connection (step B1 below).

## 6. Track B: code (this repo)

Each step has an acceptance check. Commit when it passes.

**B1. Standalone `doAs` script** (`scripts/test_doas.py`, not the MCP server)
- `impala.dbapi.connect(host, port=443, user=<machine user>, password=<workload pw>,
  auth_mechanism='LDAP', use_http_transport=True, use_ssl=True,
  http_path='cliservice?doAs=<user>')`, then `SELECT current_user()` or `SHOW TABLES`.
- Passes when: (a) an allowed user returns results, (b) a user not in the
  allowlist is **rejected**, (c) the Impala query log / Cloudera audit shows the
  impersonated user, not the machine user.
- A plain connection test already worked in the old sandbox with this pattern
  (LDAP, port 443, `cliservice`, SSL); the `doAs` variant is what must be re-proven.

**B2. Port into `impala_tools.py`**
- `get_db_connection(effective_user)` builds the `doAs` path from the user.
- Machine-user credentials come from env vars; use `IMPALA_PASSWORD_B64` for the
  password (already supported; workload passwords contain special characters that
  CML env-var injection can mangle).
- Username handling: it lands in a URL query string, so it must come only from
  the validated token (or fixed test config), match an allowlist or strict regex
  (e.g. `^[a-z][a-z0-9._-]{0,63}$`), and be URL-encoded. Reject anything else.
- Keep `execute_query` / `get_schema` signatures; thread the effective user
  through them.
- Note: the existing read-only check only looks at the first word of the query.
  Tighten it (single statement, no stacked queries) before any customer demo.

**B3. Deploy as a CML Application with a fixed test user**
- Fix `start_mcp.py`: `PROJECT_DIR` is hardcoded to
  `/home/cdsw/iceberg-mcp-server`; the project root is `/home/cdsw`.
- `start_mcp.py` (not `.sh`) is the one that works in CML: bind
  `127.0.0.1:$CDSW_APP_PORT` (CML's proxy owns `0.0.0.0`). `start_mcp.sh` still
  binds `0.0.0.0` and is out of date; delete it or fix it.
- Application setting: allow unauthenticated access.
- Set env vars in the Application config: `IMPALA_HOST`, `IMPALA_USER`
  (machine user), `IMPALA_PASSWORD_B64`, `IMPALA_DATABASE`.
- Passes when: an MCP client can call `get_schema` against the deployed URL and
  the audit log shows the fixed test user.

**B4. Entra token validation (new work, never built)**
- Use FastMCP's built-in bearer/JWT verification (JWKS URI, issuer, audience).
  `uv.lock` had pinned fastmcp 2.9.2; the auth API differs between versions, so
  **pin an exact fastmcp version in `pyproject.toml`** before starting and read
  that version's docs.
- Entra JWKS: `https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys`
  **(unverified)**. Check whether your app registration issues v1 or v2 tokens;
  the `iss` value differs and validation fails if you configure the wrong one.
- Identity claim: access tokens often lack `email`. `preferred_username` or
  `upn` is usually present; `email` needs an optional claim configured
  **(unverified)**. Decide which claim feeds the mapping.
- Behaviour: missing/invalid/expired token -> reject; unmapped user -> reject;
  never fall back to the machine user's own identity.
- Passes when: valid token for user X yields queries audited as X; an invalid
  token, wrong audience, and unmapped user are each rejected.

**B5. Test without Azure**
- Azure account and Foundry access are pending, so mint a test Entra token
  directly (test app registration, `az account get-access-token --scope
  api://<app-id>/.default`, or client-credentials) and call the MCP server with
  it.

**B6. End-to-end**
- Azure AI Foundry chatbot -> MCP -> Impala. Capture the Cloudera audit log
  entries showing the individual users. This is the demo evidence for Sofra.
- Unknown: how the Azure client attaches the token (Foundry MCP tool config vs
  custom client). Resolve once Azure access lands.

## 7. Cleanup and hygiene

- `uv.lock` removed: CML deployments use `pip install .`, which ignores it.
  Pin dependency versions in `pyproject.toml` instead (see B4).
- `pyproject.toml` requires Python >=3.12. The sandbox where this plan was
  written ran Python 3.11. Confirm the CML runtime's Python version before
  deploying, or `pip install .` will refuse.
- README's Claude Desktop / `uv` instructions were removed as irrelevant to this
  deployment.

## 8. Open questions

- What exactly went wrong with Impala-side JWT auth? (Record it here.)
- Which Entra claim is reliable for identity, and does Sofra's tenant emit it?
- Mapping: email local part vs a lookup table in Cloudera.
- How will the Azure client present the token to the MCP server?
- Token lifetime and refresh behaviour for long chatbot sessions.
- Who approves Azure account and Foundry model access, and by when?
