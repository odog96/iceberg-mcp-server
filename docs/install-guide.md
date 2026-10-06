# Install Guide — Iceberg MCP Server on Cloudera (Impala)

This guide installs and configures the **Iceberg MCP Server** on Cloudera AI (CML) and the
**Impala** virtual warehouse it queries. It covers everything from importing this repository to a
verified, per-user `doAs` deployment.

**Scope.** This guide stops at the MCP server's front door: it expects a trusted caller to present a
per-user bearer token. How that token is produced and relayed (the calling application and any
intermediary gateway) is **out of scope** here — this document is only about the MCP server and
Impala.

---

## 1. Overview & architecture

The MCP server exposes two read-only tools to an AI agent:

- `get_schema()` — list the tables in the configured database.
- `execute_query(query)` — run one read-only SQL statement and return JSON.

Every call runs **as the end user**, not as a shared service account:

```
Trusted caller
   │  Authorization: Bearer <per-user token>
   ▼
MCP server  (Cloudera AI Application)
   │  1. validate the token (signature, issuer, audience, expiry)
   │  2. map the token identity → a Cloudera username
   │  3. connect to Impala as the machine user, impersonating that username (doAs)
   ▼
Impala Virtual Warehouse (CDW)  →  Ranger authorization + audit show the end user
```

There are **two independent authorization gates** you must configure. Keeping them straight prevents
most setup problems:

| Gate | Question it answers | Where you configure it |
|---|---|---|
| **doAs allowlist** | *May the machine user impersonate this user?* | Impala coordinator (`authorized_proxy_user_config`) — §4c |
| **Ranger** | *May that user read this table?* | Ranger policies — §4d |

A user must pass **both**. The MCP server adds its own checks on top (token validity, identity
mapping, optional allowlist).

---

## 2. Prerequisites

- A **Cloudera AI (CML) workspace** you can create a project in, with outbound access to PyPI.
- **CDP admin rights** to create a machine (service) user and sync it to the environment.
- Each **end user** you intend to impersonate must be (or become) a synced workload user in that
  environment — not a Cloudera AI user (see §4e).
- An **Impala Virtual Warehouse** in Cloudera Data Warehouse (or the ability to create one).
- For production (per-user tokens): an **Entra ID app registration** representing this server — you
  need its **Application ID URI** (the token *audience*) and the **tenant ID**. (Registering it is
  out of scope; you only consume these two values here.)
- A free application **subdomain** in the workspace (the template uses `iceberg-mcp`).
- Runtime: **PBJ Workbench, Python 3.12, Standard** (set in `.project-metadata.yaml`).

---

## 3. Identity & access model (read this first)

**How a token becomes a Cloudera user.** After the token is validated, the server maps its identity
claim to a Cloudera username:

- **Default:** the local part of the first present claim among `preferred_username`, `upn`, `email`
  — e.g. `jgaragorry@cloudera.com` → `jgaragorry`. If your email handle already equals the Cloudera
  username, **no mapping configuration is needed**.
- **`USER_MAP`** (optional): explicit `identity=cloudera_user` pairs for when the handle differs,
  e.g. `USER_MAP=oliver.zarate@corp.com=ozarate`. When set it is **exclusive** — identities not
  listed are rejected.
- **`ENTRA_USER_CLAIMS`** (optional): override which claims are tried, in order.

**The machine user + `doAs`.** The server authenticates to Impala as one machine user (LDAP, over
HTTPS) and asks Impala to run each statement as the mapped end user via
`http_path=cliservice?doAs=<user>`. Impala's `authorized_proxy_user_config` decides who the machine
user is allowed to impersonate — the second gate behind the server's own checks.

---

## 4. Cloudera-side setup (Impala)

### 4a. Machine (service) user
1. Create the machine user if it does not exist (needs CDP admin).
2. In the target **Environment**, grant it **EnvironmentUser** and **DWUser**.
3. **Environment → Actions → Sync Users**; wait for completion so FreeIPA has the account.
4. Set the machine user's **workload password** (per-user, in the CDP profile). This is the password
   the server logs in with.

### 4b. Impala Virtual Warehouse
1. Create a Virtual Warehouse in the environment.
2. **Type must be Impala** (the console often defaults to Hive).
3. **Enable JWT Authentication = off.** The MCP server validates tokens in code; Impala itself
   authenticates the machine user by LDAP password, not JWT.
4. Note the **coordinator host** (e.g. `coordinator-<name>.<env>.<region>.cloudera.site`). This is
   `IMPALA_HOST` later — without the `https://` prefix.

### 4c. doAs proxy config  ⚠️ most common failure point
1. In the VW: **Configurations → Impala Coordinator**.
2. Set the top dropdown to **flagfile**.
3. Add a custom configuration:
   - **Key:** `authorized_proxy_user_config`
   - **Value:** `<env-system-account>=*;<machine-user>=user1,user2,user3`

   Example:
   ```
   srv_env-rmcm57=*;srv_srv_mcp_proxy=ozarate,jgaragorry,fcobo
   ```

   Two mistakes that cause `User '<machine-user>' is not authorized to delegate to '<user>'`:
   - **Wrong spelling of the machine user.** It must match the account that actually authenticates,
     exactly (short name, no domain). A single extra character fails silently until a call is made.
   - **Overwriting the environment system account entry.** The value is `;`-separated. Read the
     existing value and **append** your `machine-user=...` rule — do not replace the `...=*` entry
     that is already there.
4. **Save and restart** the warehouse so the coordinator reloads the flag. (A soft refresh may not
   pick up startup flags.)

### 4d. Ranger grants
Each impersonated user needs, in the **Hadoop SQL** (Impala) Ranger service, on the database and
tables you will query:
- **SELECT** on the database → tables → columns, and
- the database-level privilege so the database/tables appear in `SHOW DATABASES` / `SHOW TABLES`.

Without this, `doAs` connects fine but `SHOW TABLES` returns nothing and `SELECT` is denied.

### 4e. End users (the doAs targets): environment access
The users you impersonate must be **real, recognized workload identities in the same CDP
environment**, or Impala cannot resolve them for `doAs`. In the **CDP Management Console**:
1. Each end user exists as a CDP user (created there, or synced from your identity provider).
2. Each is granted access to the **Environment** (e.g. the **EnvironmentUser** resource role) and is
   included when you run **Environment → Actions → Sync Users**, so the workload layer (FreeIPA)
   knows the account.
3. Each has the **Ranger** data grants from §4d.

**What the end users do _not_ need:**
- **Cloudera AI (CML) access.** They never sign in to CML. Their identity arrives only as a token
  claim that the server maps to a username and passes to Impala as `doAs`. Only the person who
  **deploys and runs the MCP application** needs Cloudera AI access.
- **Their own Impala/CDW login.** They are impersonated by the machine user (§4a), which is the
  account that actually authenticates; the end users do not open their own session.

> Whether your environment *also* requires the end users to hold a Data Warehouse access role (beyond
> EnvironmentUser) can vary by CDP version. Verify empirically with `scripts/test_doas.py` (§7,
> Layer 1): `--allowed` users that are set up correctly connect; anything missing fails there, before
> the MCP server is involved.

---

## 5. Deploy the MCP server (this repo)

### 5a. Import the project
In Cloudera AI: **New Project → Git**, paste this repository's URL, then **Configure Project**. The
included `.project-metadata.yaml` turns the repo into a template and prompts for the variables below.

### 5b. Configure environment variables

**Connection (always required):**

| Variable | Value |
|---|---|
| `IMPALA_HOST` | Coordinator host from §4b, no `https://` |
| `IMPALA_PORT` | `443` for Cloudera Data Warehouse |
| `IMPALA_USER` | The machine user from §4a |
| `IMPALA_PASSWORD_B64` | Machine user's workload password, base64-encoded: `printf %s 'PASSWORD' \| base64 -w0` |
| `IMPALA_DATABASE` | Database to query (`default`, or `mcp_demo` after §6) |

**Choose exactly one identity mode:**

| Mode | Set | Behavior |
|---|---|---|
| **Production (per-user tokens)** | `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` (the app's Application ID URI). For v1 tokens also `ENTRA_ISSUER=https://sts.windows.net/<tenant>/` | Validates each caller's token and runs as the mapped user. `MCP_TEST_USER` is ignored. |
| **Test (no token check)** | `MCP_TEST_USER` only | Every call runs as this one Cloudera user. No token required. For validation only — see §8. |

> The server refuses to start unless either `ENTRA_TENANT_ID`+`ENTRA_AUDIENCE` **or** `MCP_TEST_USER`
> is set.

**Optional hardening / mapping:**

| Variable | Purpose |
|---|---|
| `USER_MAP` | Explicit `identity=cloudera_user` pairs (see §3) |
| `ENTRA_USER_CLAIMS` | Claims tried for identity (default `preferred_username,upn,email`) |
| `ALLOWED_USERS` | Comma-separated Cloudera usernames; any other mapped user is rejected |
| `ENTRA_REQUIRED_SCOPE` | Require this scope on the token (e.g. `access_as_user`) |
| `MCP_ENABLE_WHOAMI` | `1` exposes a `whoami` diagnostic tool (non-secret token details; never the token) |

### 5c. Launch
Launch the **Iceberg MCP Server** application. On start, `start_mcp.py` installs the package and
serves MCP over HTTP. The endpoint is the **application URL + `/mcp`**.

> The CML Application is set to `bypass_authentication: true` because an external caller cannot use
> CML's own sign-in. In production the **token check inside the MCP server is the only gate**, so it
> must be deployed with `ENTRA_*` configured (see §8).

---

## 6. (Optional) Load sample data to validate

To prove the pipeline with throwaway data, run the **Load MovieLens sample data** job (Jobs page).
It creates database `mcp_demo` (4 Iceberg tables, ~124k rows) via `scripts/setup_sample_data.py`:

```
python scripts/setup_sample_data.py --user <cloudera-user>   # --user needs CREATE in Ranger
```

The job writes **as the impersonated user**, so that user needs CREATE on the database and the
machine user must be allowed to impersonate them (§4c). Afterward, set `IMPALA_DATABASE=mcp_demo` and
restart the application.

---

## 7. Verification & smoke tests (bottom-up)

Test from the lowest layer up, so a failure points at the right place.

**Layer 1 — Impala `doAs` (no MCP server involved).** With connection details in the environment or
`impala_details.txt`:
```
python scripts/test_doas.py --allowed ozarate,jgaragorry,fcobo --denied someone_not_allowed --explore
```
Confirms: a plain connection works; each allowed user's `SELECT effective_user()` returns that user;
a denied user is rejected; and (with `--explore`) what each user can actually read.

**Layer 2 — MCP server reachability (no token).**
```
scripts/curl_mcp.sh https://<application-url> health      # expect: HTTP 200
scripts/curl_mcp.sh https://<application-url> list        # token server: HTTP 401 (correct)
```
A `401` from `list` on a production server is the **right** answer — it means the token gate is live.

**Layer 3 — full path with a real token.**
```
python scripts/get_entra_token.py --tenant <tenant> --server-id <app-id> --client-id <client-app-id>
python scripts/call_mcp.py https://<application-url>/mcp whoami        --token-file .entra_token --claims
python scripts/call_mcp.py https://<application-url>/mcp get_schema    --token-file .entra_token
python scripts/call_mcp.py https://<application-url>/mcp execute_query --query "SELECT COUNT(*) FROM mcp_demo.movies" --token-file .entra_token
```
`whoami` confirms the identity mapping (`mapped_cloudera_user`); `get_schema` and `execute_query`
confirm the full chain through `doAs` to the data.

---

## 8. Security considerations

- **The MCP token check is the only gate.** Because the CML Application bypasses CML auth, a
  production deployment **must** set `ENTRA_*`. Do not expose a `MCP_TEST_USER` server beyond a
  controlled test — anyone with the URL would run as that user.
- **Deny by default.** Unmapped identities, invalid/expired tokens, and (if set) missing scope or
  non-allowlisted users are all rejected.
- **Read-only.** The server rejects writes and stacked statements; `doAs` + Ranger enforce the rest.
- **Secrets.** Store the machine-user password as `IMPALA_PASSWORD_B64`. Never commit credentials;
  keep connection files (`impala_details.txt`, token files) gitignored.
- **Stop idle test apps.** A no-token test deployment should be stopped when not in use.

---

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `HTTP 503` from the coordinator | Virtual Warehouse is stopped or restarting. Wait for Running. |
| `User '<machine-user>' is not authorized to delegate to '<user>'` | `authorized_proxy_user_config` missing, misspelled machine user, user not listed, or the env system-account entry was overwritten (§4c). Restart after fixing. |
| `doAs` fails for a user that *is* listed in the proxy config | The impersonated user isn't a recognized workload identity in the environment — create/sync it in the Management Console (§4e). Confirm with `test_doas.py`. |
| `SHOW TABLES` empty but `SELECT` works | Ranger: the user lacks the show/SELECT grant on that database (§4d). (Right after creation, allow a moment for catalog propagation.) |
| `HTTP 401` from the MCP server | Missing/expired/invalid token, or token audience/issuer doesn't match `ENTRA_AUDIENCE`/`ENTRA_ISSUER` (watch v1 `sts.windows.net` vs v2 issuer). |
| Mapped to the wrong / no Cloudera user | Email handle ≠ Cloudera username → set `USER_MAP`; or the expected claim isn't present → set `ENTRA_USER_CLAIMS`. Use `whoami` to see what the token carries. |
| A custom client script tries to delegate unexpectedly | Some environments set `HADOOP_USER_NAME`, which the Impala client turns into a `doAs`. Launch such scripts with `env -u HADOOP_USER_NAME …`. (Does not affect the MCP server, which sets `doAs` explicitly.) |

---

## 10. Appendix

**Endpoint.** `https://<application-url>/mcp`

**doAs config template.**
```
<env-system-account>=*;<machine-user>=user1,user2,user3
```

**Base64 a password.**
```
printf %s 'YOUR_PASSWORD' | base64 -w0
```

**Token/identity boundary contract.** The server expects `Authorization: Bearer <JWT>` where the JWT
is signed by the tenant in `ENTRA_TENANT_ID`, has audience `ENTRA_AUDIENCE`, is unexpired, and
carries an identity claim (`preferred_username` / `upn` / `email`) whose handle (or `USER_MAP` entry)
is a valid Cloudera username.

**Related docs.** `README.md` (quick start), `docs/build-plan.md` (architecture & runbook),
`docs/entra-app-registration-checklist.md` (Entra app registration, out of scope here).
