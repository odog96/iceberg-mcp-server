# Cloudera Iceberg MCP Server (via Impala)

This is a A Model Context Protocol server that provides read-only access to Iceberg tables via Apache Impala. This server enables LLMs to inspect database schemas and execute read-only queries.

- `execute_query(query: str)`: Run a single read-only SQL query on Impala and return the results as JSON.
- `get_schema()`: List all tables available in the current database.

Both tools run as the **end user**: the server validates the caller's Entra ID bearer token, maps it to a Cloudera username, and connects to Impala as a machine user impersonating that username (`doAs`), so Ranger and the audit log see the individual user.

## Deployment

This fork is deployed as a Cloudera Machine Learning (CML) Application using `start_mcp.py`, which installs the package with `pip` and serves MCP over HTTP. See `docs/build-plan.md` for the architecture, setup runbook and build steps.

## Deploy on Cloudera AI (quick start, test mode)

`.project-metadata.yaml` turns this repo into a Cloudera AI project template. It deploys the
**unauthenticated test version**: every caller runs as one fixed Cloudera user and no token is
checked. (For per-user identity with Entra ID tokens, see `docs/entra-app-registration-checklist.md`.)

1. In Cloudera AI: **New Project > Git**, paste this repo's URL, then **Configure Project**.
2. Fill in the environment variables it asks for:

   | Variable | What to enter |
   |---|---|
   | `IMPALA_HOST` | Impala coordinator host, no `https://` |
   | `IMPALA_USER` | The machine user that logs in to Impala |
   | `IMPALA_PASSWORD_B64` | Its password, base64-encoded: `printf %s 'PASSWORD' | base64 -w0` |
   | `MCP_TEST_USER` | The Cloudera user every request runs as |
   | `IMPALA_PORT`, `IMPALA_DATABASE` | Defaults are `443` and `default` |

3. Launch. It installs dependencies, creates an optional sample-data job (not run automatically),
   and starts the application **Iceberg MCP Server**. The MCP endpoint is the application's URL plus `/mcp`.
4. Check it: `scripts/curl_mcp.sh https://<application-url> list` (see below).

Before you start, on the Impala side: the machine user must be allowed to impersonate
`MCP_TEST_USER` (`authorized_proxy_user_config`), and that user needs Ranger access to the tables
(see `docs/build-plan.md`, Track A). To try it with demo data, run the **Load MovieLens sample data**
job, then set `IMPALA_DATABASE` to `mcp_demo` and restart the application.

Things to know:
- **Unauthenticated applications can be blocked** by a workspace admin ("unauthenticated access to
  applications prevented by site-wide configuration"). An admin has to allow it.
- Anyone with the URL can run read-only queries as `MCP_TEST_USER`. Stop the application when idle.
- The application subdomain `iceberg-mcp` must be unused in the workspace, and the workspace must
  be able to reach PyPI. The runtime is set to PBJ Workbench, Python 3.12, Standard (edit `runtimes:` if needed).
- The template is checked by tests (`tests/test_project_metadata.py`), but the first import into a
  fresh workspace is the real test.

## Check the server with curl

```
scripts/curl_mcp.sh https://<application-url> health                       # is it up? (no token needed)
scripts/curl_mcp.sh https://<application-url> list                         # which tools does it offer?
scripts/curl_mcp.sh https://<application-url> call get_schema
scripts/curl_mcp.sh https://<application-url> call execute_query '{"query":"SELECT effective_user()"}'
```

A server that checks tokens needs one: `TOKEN=<token> scripts/curl_mcp.sh ...` (or `TOKEN_FILE=<path>`).
No token gives `HTTP 401`, which is the correct answer from such a server. The script needs only
`curl`. An MCP server expects a short conversation first (initialize, confirm, then your request),
which the script does for you; the three steps are described at the top of the script.

## Configuration

| Variable | Purpose |
|---|---|
| `IMPALA_HOST`, `IMPALA_PORT` (443), `IMPALA_DATABASE` | Impala coordinator |
| `IMPALA_USER` | Machine user that authenticates (and is allowed to impersonate via `authorized_proxy_user_config`) |
| `IMPALA_PASSWORD_B64` (or `IMPALA_PASSWORD`) | Machine user workload password, base64 preferred |
| `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` | Enable Entra token validation. Audience is the app registration's Application ID URI |
| `ENTRA_ISSUER`, `ENTRA_JWKS_URI` | Optional overrides (default to the v2.0 issuer/keys for the tenant; use `https://sts.windows.net/<tenant>/` for v1 tokens) |
| `ENTRA_USER_CLAIMS` | Claims tried in order for identity (default `preferred_username,upn,email`); the local part becomes the Cloudera username |
| `USER_MAP` | Optional `identity=cloudera_user` pairs (comma-separated) for tokens whose identity is not a Cloudera name. When set it is exclusive: unlisted identities are rejected |
| `ENTRA_REQUIRED_SCOPE` | Optional. If set (e.g. `access_as_user`), tokens without that permission are rejected. Off by default: turn on after `whoami` shows what real callers send |
| `MCP_ENABLE_WHOAMI` | Optional. `1` adds a `whoami` tool that shows non-secret details of the caller's verified token (never the token). For demos and debugging |
| `ALLOWED_USERS` | Optional comma-separated allowlist of Cloudera usernames |
| `MCP_TEST_USER` | **Test deployments only:** skip token validation and run every call as this user. Ignored when `ENTRA_*` is set |

The server refuses to start unless `ENTRA_TENANT_ID`+`ENTRA_AUDIENCE` or `MCP_TEST_USER` is set.

## Testing

```
pip install -e . --group dev      # everything comes from pyproject.toml
pytest                                   # unit + end-to-end auth tests, no Cloudera needed
python scripts/test_doas.py --allowed <user> --denied <user>   # against a real Impala VW
python scripts/get_entra_token.py                               # real Entra sign-in (device code) -> .entra_token
python scripts/call_mcp.py <url> get_schema --token-file .entra_token --claims   # against a deployed server
```

## Usage with AI frameworks

The `./examples` folder contains several examples how to integrate this MCP Server with common AI Frameworks like LangChain/LangGraph, OpenAI SDK.

### Transport

The MCP server's transport protocol is configurable via the `MCP_TRANSPORT` environment variable. Supported values:
- `stdio` **(default)** — communicate over standard input/output. Useful for local tools, command-line scripts, and integrations with clients like Claude Desktop.
- `http` - expose an HTTP server. Useful for web-based deployments, microservices, exposing MCP over a network.
- `sse` — use Server-Sent Events (SSE) transport. Useful for existing web-based deployments that rely on SSE.


*Copyright (c) 2025 - Cloudera, Inc. All rights reserved.*
