# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastMCP server that gives an external AI agent (e.g. an Azure Foundry/Copilot chatbot) read-only access to Impala/Iceberg tables, where **every query runs as the individual end user** so Ranger and Impala's audit log show that user and not a shared service account. That per-user auditability is the core requirement. Any change that could let a query run as the machine user, or as a user the caller didn't prove, is a security regression. `docs/build-plan.md` holds the architecture, current status (what is proven against real Impala/Entra and what is only unit-tested) and the deployment runbook. Read it before larger changes.

## Commands

```
pip install -e . --group dev                      # deps (incl. dev group) from pyproject.toml
pytest                                            # all tests; no Cloudera/Azure needed
pytest tests/test_impala_tools.py::test_readonly_allowed   # single test
ruff check src tests && ruff format src tests     # line-length 120
python -m iceberg_mcp_server.server               # run locally (MCP_TRANSPORT=stdio|http|sse, default stdio)
```

The server refuses to start unless either `ENTRA_TENANT_ID`+`ENTRA_AUDIENCE` (production) or `MCP_TEST_USER` (test mode, no token check) is set. See README's Configuration table for all env vars.

Live checks against real infrastructure (these need credentials, so don't run them unprompted):
- `python scripts/test_doas.py --allowed <u> --denied <u>`: proves machine-user impersonation against a real Impala VW.
- `scripts/curl_mcp.sh <app-url> health|list|call <tool> '<json>'`: checks a deployed server; `TOKEN=`/`TOKEN_FILE=` for token-checking servers.
- `scripts/get_entra_token.py` / `scripts/call_mcp.py`: real Entra sign-in and authenticated tool calls.

## Architecture

Request flow: Entra bearer token -> FastMCP `JWTVerifier` -> claims mapped to a Cloudera username -> Impala connection as machine user with `doAs=<username>`.

- `src/iceberg_mcp_server/server.py`: FastMCP app. Tools `execute_query`, `get_schema` and an opt-in `whoami` (`MCP_ENABLE_WHOAMI=1`). Each tool resolves the effective user via `_effective_user()` before touching Impala. Also has unauthenticated `/` and `/health` routes for CML's health poll. These routes must stay static and must not touch Impala.
- `src/iceberg_mcp_server/identity.py`: builds the `JWTVerifier` from `ENTRA_*` (FastMCP validates signature/issuer/audience/expiry/scope before any tool runs). It maps claims to a username: the local part of the first present claim in `ENTRA_USER_CLAIMS`, or an exclusive `USER_MAP` lookup, then the `ALLOWED_USERS` allowlist. Everything denies by default. `MCP_TEST_USER` is honoured only when Entra auth is *not* configured.
- `src/iceberg_mcp_server/tools/impala_tools.py`: Impala access via impyla (LDAP over HTTPS). The effective user is put into `http_path` as `?doAs=<user>`, so `validate_username` (strict regex) plus URL-encoding are what prevent query-string injection. `get_db_connection` deliberately has no default effective user. `check_readonly` is a defence-in-depth SQL guard that strips comments/strings/quoted identifiers, then requires a single `SELECT/WITH/SHOW/DESCRIBE` statement with no write keywords. The real gates are Impala's `authorized_proxy_user_config` and Ranger.
- Tool errors are returned as strings (`"Error: ..."`), not raised; identity failures are raised as `PermissionError`.

Tests: `test_impala_tools.py` / `test_identity.py` are unit tests with mocked `connect`. `test_e2e_auth.py` spins up a real FastMCP HTTP server with a local JWKS standing in for Entra and records which user each Impala connection would impersonate (some cases need `curl`). `test_project_metadata.py` guards `.project-metadata.yaml`, e.g. it must never set `ENTRA_*` and must have no password default.

## Deployment (Cloudera AI / CML)

- Runs as a CML Application via `start_mcp.py`, which pip-installs the project into system Python and runs it with `MCP_TRANSPORT=http`, binding `127.0.0.1:$CDSW_APP_PORT` (not `0.0.0.0`: CML's proxy owns that). It is a `.py` file, not a shell script, and it avoids `__file__`, because CML runs app scripts through the Python kernel.
- CML mounts the project root at `/home/cdsw`. `start_mcp.py` and `scripts/install_deps.py` look for the repo there or in a direct subfolder (e.g. `/home/cdsw/iceberg-mcp-server`), and `MCP_PROJECT_DIR` overrides that. When the repo is a subfolder, the Application's script path is `iceberg-mcp-server/start_mcp.py`.
- `.project-metadata.yaml` is an AMP template that deploys the **unauthenticated test mode** only (`MCP_TEST_USER`, `bypass_authentication: true`).
- Prefer `IMPALA_PASSWORD_B64` over `IMPALA_PASSWORD`: CML's env injection can mangle `$` in values.

## Repo conventions

- Never commit credentials or connection details. `.gitignore` already covers `.env*`, `impala_*.txt`, `.entra_token*`, etc. Images are git-ignored except under `docs/images/`, where the install guide's screenshots live (crop out CRNs and other environment details before adding one).
- `docs/build-plan.md` asks for a commit after every completed step, and its status table should be updated when something moves from "unproven" to "proven" against real infrastructure.
