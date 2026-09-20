# Cloudera Iceberg MCP Server (via Impala)

This is a A Model Context Protocol server that provides read-only access to Iceberg tables via Apache Impala. This server enables LLMs to inspect database schemas and execute read-only queries.

- `execute_query(query: str)`: Run a single read-only SQL query on Impala and return the results as JSON.
- `get_schema()`: List all tables available in the current database.

Both tools run as the **end user**: the server validates the caller's Entra ID bearer token, maps it to a Cloudera username, and connects to Impala as a machine user impersonating that username (`doAs`), so Ranger and the audit log see the individual user.

## Deployment

This fork is deployed as a Cloudera Machine Learning (CML) Application using `start_mcp.py`, which installs the package with `pip` and serves MCP over HTTP. See `docs/build-plan.md` for the architecture, setup runbook and build steps.

## Configuration

| Variable | Purpose |
|---|---|
| `IMPALA_HOST`, `IMPALA_PORT` (443), `IMPALA_DATABASE` | Impala coordinator |
| `IMPALA_USER` | Machine user that authenticates (and is allowed to impersonate via `authorized_proxy_user_config`) |
| `IMPALA_PASSWORD_B64` (or `IMPALA_PASSWORD`) | Machine user workload password, base64 preferred |
| `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` | Enable Entra token validation. Audience is the app registration's Application ID URI |
| `ENTRA_ISSUER`, `ENTRA_JWKS_URI` | Optional overrides (default to the v2.0 issuer/keys for the tenant; use `https://sts.windows.net/<tenant>/` for v1 tokens) |
| `ENTRA_USER_CLAIMS` | Claims tried in order for identity (default `preferred_username,upn,email`); the local part becomes the Cloudera username |
| `ALLOWED_USERS` | Optional comma-separated allowlist of Cloudera usernames |
| `MCP_TEST_USER` | **Test deployments only:** skip token validation and run every call as this user. Ignored when `ENTRA_*` is set |

The server refuses to start unless `ENTRA_TENANT_ID`+`ENTRA_AUDIENCE` or `MCP_TEST_USER` is set.

## Testing

```
pip install -e . pytest pytest-asyncio "pyjwt[crypto]"
pytest                                   # unit + end-to-end auth tests, no Cloudera needed
python scripts/test_doas.py --allowed <user> --denied <user>   # against a real Impala VW
python scripts/call_mcp.py <url> get_schema --token "$TOKEN"   # against a deployed server
```

## Usage with AI frameworks

The `./examples` folder contains several examples how to integrate this MCP Server with common AI Frameworks like LangChain/LangGraph, OpenAI SDK.

### Transport

The MCP server's transport protocol is configurable via the `MCP_TRANSPORT` environment variable. Supported values:
- `stdio` **(default)** — communicate over standard input/output. Useful for local tools, command-line scripts, and integrations with clients like Claude Desktop.
- `http` - expose an HTTP server. Useful for web-based deployments, microservices, exposing MCP over a network.
- `sse` — use Server-Sent Events (SSE) transport. Useful for existing web-based deployments that rely on SSE.


*Copyright (c) 2025 - Cloudera, Inc. All rights reserved.*
