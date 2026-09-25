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

Architecture diagrams are kept out of the repo (git ignores images); the flow is described below.

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

## 3. Status

Updated 2026-09-20 after the rebuild session. "Code done" means implemented and
covered by tests that need no Cloudera or Azure (`pytest`, 65 tests); "unproven"
means it has never run against a real Impala VW or a real Entra tenant.

| Piece | State |
|---|---|
| Track A (machine user, VW, `authorized_proxy_user_config`) | **Done and proven** 2026-09-21: `srv_srv_mcp_proxy` impersonates ozarate, jgaragorry, fcobo, jcaseiro; a non-allowlisted user is rejected |
| Sample data | `mcp_demo` (MovieLens small, 4 tables, ~124k rows) loaded by `scripts/setup_sample_data.py`; all 4 users can read it |
| B1 `scripts/test_doas.py` | **Proven** against the real VW (see Track A row); the query-log/audit check is a manual step |
| B2 `doAs` in `impala_tools.py` (validated username, URL-encoded, tighter read-only guard) | Code done, unit-tested with a mocked `connect`; **unproven** against Impala |
| B3 `start_mcp.py` path fix, `start_mcp.sh` removed | **Proven**: CML Application `app1` (unauthenticated, `MCP_TEST_USER=ozarate`, no token check) answers `get_schema` and `execute_query` as ozarate; writes and stacked statements are refused. Created via `cmlapi` (see below) |
| B4 Entra validation + identity mapping (`identity.py`) | Code done. Tested end to end against a local JWKS: valid, expired, wrong audience/issuer/key, no token, unmapped user. **Unproven** against real Entra (v1 vs v2 issuer, which claim is present) |
| B5 `scripts/call_mcp.py` | Done; needs a real token |
| B6 Foundry chatbot end to end | Blocked on Azure access |
| Machine user / VW in current sandbox | Not possible: no admin rights. Track A needs an environment with admin |

**Workspace policy:** CML can block unauthenticated Applications site-wide
(`unauthenticated access to applications prevented by site-wide configuration`).
An admin has to allow it; without it the external Azure client cannot reach the
MCP server at all. Recorded because it was a hard blocker on 2026-09-21.

**Creating `app1` from a session:** `cmlapi.default_client().create_application(...)`
with `bypass_authentication=True`, `script="start_mcp.py"`, runtime
`ml-runtime-pbj-workbench-python3.12-standard`, and env vars `IMPALA_HOST`,
`IMPALA_PORT`, `IMPALA_USER`, `IMPALA_PASSWORD_B64`, `IMPALA_DATABASE=mcp_demo`,
`MCP_TEST_USER`, `MCP_TRANSPORT=http`. URL is `https://<subdomain>.<CDSW_DOMAIN>/mcp`.
The Application config holds the password, so treat project access accordingly.
While `MCP_TEST_USER` is set, anyone with the URL can run read-only queries as
that user: stop the app when not testing.

## B6 done and rigorously proven: real Foundry agent -> app1 -> Impala, end to end

2026-09-22. First pass (get_schema, DESCRIBE movies, DESCRIBE links) came back with a
plausible-looking answer, but a check of `sys.impala_query_log` showed **no corresponding
queries reached Impala at all** in that window. The schema Copilot returned matched the
well-known MovieLens column layout closely enough that it's plausible the model answered from
its own training knowledge (given only 4 table names from a real `get_schema` call) rather than
from genuine `DESCRIBE` results. Caveat for future demos: **this agent's tool-call claims are
not automatically trustworthy — cross-check the audit log**, at least until this is better
understood. Not a server-side problem: our own scripted calls always show up in the log
immediately (sub-second).

To rule out fabrication/cache definitively, asked Copilot to run a query with an answer no LLM
could guess or remember: `SELECT effective_user(), now()`. Result, cross-referenced against
`sys.impala_query_log` in real time via a live poll:

| Source | Value |
|---|---|
| Copilot's chat reply | `effective_user(): ozarate`, `now(): 2026-09-22 16:55:16.447978` |
| `sys.impala_query_log` (independent, queried live) | `ozarate`, `srv_srv_mcp_proxy`, `2026-09-22 16:55:16.448073`, `SELECT effective_user(),now()` |

Match to within 95 microseconds (query-submission vs. execution-time skew) — not fabricable.
This is the customer requirement from section 1, proven end to end: a real external chatbot
(Foundry/Copilot) queries Cloudera through the MCP server, and the individual end user
(`ozarate`, not the machine user `srv_srv_mcp_proxy`) shows up in Impala's own audit log.

Useful technique for future spot-checks: `sys.impala_query_log` (columns include `db_user`,
`db_user_connection`, `sql`, `start_time_utc`) can be queried directly instead of hunting
through a portal UI — same connection path as everything else in this repo.

## Reproducible deploy and curl check

2026-09-24. `.project-metadata.yaml` (format checked against Cloudera's AMP project specification,
https://docs.cloudera.com/machine-learning/cloud/applied-ml-prototypes/topics/ml-amp-project-spec.html)
deploys the unauthenticated test version in a new workspace: installs dependencies, creates (but does
not run) an optional sample-data job, and starts the application with `bypass_authentication: true`.
Variables it asks for are the `IMPALA_*` connection settings plus `MCP_TEST_USER`. It never sets
`ENTRA_*`, and `tests/test_project_metadata.py` enforces that, along with the script paths, required
task fields and the "no password default" rule. **Not yet imported into a second workspace**, so
treat the first import as the real test.

`scripts/curl_mcp.sh` checks any MCP server with plain curl (health, list tools, call a tool, with an
optional bearer token). Verified live against the fixed-user app, against the token-checking app
(clear 401 message) and by automated tests against the local test server, including the token path.

## whoami: proving what the agent actually sends

2026-09-23. A colleague questioned whether Azure sends a token at all. Evidence so far was
indirect (app1 refuses calls with no/bad token, yet the Foundry agent's calls succeeded as
`ozarate`). To show it directly, `app1` now runs with `MCP_ENABLE_WHOAMI=1`, which adds a
`whoami` tool. It returns the verified token's non-secret details: issuer, audience, calling app
id, token version, granted scope, sign-in methods, issue/expiry times, identity claims, and the
mapped Cloudera user. It never returns the token, and omits object id, subject, IP and session ids.

Demo: ask the Foundry agent "Use the iceberg-mcp tool called whoami and show me exactly what it
returns." (A new chat may be needed so the agent picks up the new tool.) Expected: `token_received`
true, `requesting_app_id` = `ac7b4ab5-79c3-4962-9c9a-3131a90f2217` (our client app, i.e. Foundry's
sign-in), `audience` = `api://1217683d-...`, `mapped_cloudera_user` = `ozarate`.

Also added, off by default: `ENTRA_REQUIRED_SCOPE=access_as_user` rejects tokens that lack that
permission. Turn it on after `whoami` confirms Foundry's token carries `scp: access_as_user`.

## App roles swapped: app1 = real Entra checking, app2 = legacy fixed user

2026-09-22, after connecting Foundry: Azure does not allow editing an OAuth tool connection
once created ("OAuth doesn't support updating the configuration"), and the Foundry connection
had already been pointed at `app1`'s URL. Rather than recreate the Foundry connection, the two
CML Applications' configs were swapped instead, so Foundry needed no further changes:

| App | URL | Role |
|---|---|---|
| **app1** | `app1-mcp-9368e6...` | **Real Entra token checking.** This is what Foundry's `iceber-mcp` connection points at. |
| app2 | (address deliberately not recorded here) | Legacy fixed test user (`MCP_TEST_USER=ozarate`), no token check. **Stopped 2026-09-24** because the repo is public and this app has no access control; restart it from the Applications page only when needed, then stop it again. |

Verified end to end after the swap, with a fresh token (device-code sign-in as
oliverzarate@ymail.com):
- `app1` no token -> HTTP 401
- `app1` garbage token -> HTTP 401
- `app1` real token -> `get_schema` and `execute_query` succeed, `effective_user()` = `ozarate`.
  Query tagged `swap-verify-145955` for the audit-log check.
- `app2` still works with no token, fixed to `ozarate` (legacy behavior intact)

Env vars (now on app1; see `docs/entra-app-registration-checklist.md` for how the IDs were
obtained):
```
ENTRA_TENANT_ID=650a1000-e5e3-40bf-97a9-d62002a0934b
ENTRA_AUDIENCE=api://1217683d-abaf-41ff-bbf7-c53a0a8814a9
ENTRA_ISSUER=https://sts.windows.net/650a1000-e5e3-40bf-97a9-d62002a0934b/
USER_MAP=oliverzarate@ymail.com=ozarate
```

Lesson for next time: decide the final CML Application URL *before* connecting it to a Foundry
OAuth tool, since that connection can't be edited afterward, only replaced.

Two surprises worth remembering:
- **This tenant issues v1 tokens** (`ver: 1.0`, issuer `sts.windows.net/...`) even though the
  server app's manifest was set to `requestedAccessTokenVersion: 2`. v1 tokens have no
  `preferred_username`/`upn`; only `email` is present, which is why `USER_MAP` was needed
  instead of the local-part default. Sofra's tenant may behave differently; check `ver` on a
  real token before assuming v2.
- `scripts/verify_real_token.py` checks a saved token against `identity.py` with no server or
  CML deploy needed. Much faster than redeploying to catch issuer/audience mismatches.

Remaining critical path: Track A -> run B1 -> deploy (B3) with `MCP_TEST_USER`
-> switch to `ENTRA_*` and test with a real token (B5) -> B6.

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
- `pyproject.toml` now requires Python >=3.10 (was 3.12) and pins
  `fastmcp==2.14.7`, which is what the tests ran against on Python 3.10.
- README's Claude Desktop / `uv` instructions were removed as irrelevant to this
  deployment.

## 8. Open questions

- What exactly went wrong with Impala-side JWT auth? (Record it here.)
- Which Entra claim is reliable for identity, and does Sofra's tenant emit it?
- Mapping: email local part vs a lookup table in Cloudera.
- How will the Azure client present the token to the MCP server?
- Token lifetime and refresh behaviour for long chatbot sessions.
- Who approves Azure account and Foundry model access, and by when?
