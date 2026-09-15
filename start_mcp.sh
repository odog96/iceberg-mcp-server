#!/bin/bash
# Startup script for running the Cloudera Iceberg MCP Server as a CML Application.
#
# CML builds Applications inside an isolated container per run, so there is no
# need for a nested virtualenv/uv env: dependencies are installed straight into
# the system Python. CML injects the externally-routable port via CDSW_APP_PORT;
# we map that onto FastMCP's own FASTMCP_PORT/FASTMCP_HOST settings.
set -euo pipefail

cd "$(dirname "$0")"

: "${CDSW_APP_PORT:?CDSW_APP_PORT is not set - this script expects to run as a CML Application}"

echo "Installing iceberg-mcp-server dependencies into system Python..."
pip3 install --no-cache-dir --disable-pip-version-check .

export MCP_TRANSPORT="${MCP_TRANSPORT:-http}"
export FASTMCP_HOST="0.0.0.0"
export FASTMCP_PORT="${CDSW_APP_PORT}"
export PYTHONUNBUFFERED=1

echo "Starting Iceberg MCP Server (transport=${MCP_TRANSPORT}) on ${FASTMCP_HOST}:${FASTMCP_PORT}"
# Invoked as a module, not the "run-server" console script: pip installs consoles
# scripts to a per-user bin dir that is only on PATH in an interactive shell, not
# in a freshly-started CML Application container.
exec python3 -m iceberg_mcp_server.server
