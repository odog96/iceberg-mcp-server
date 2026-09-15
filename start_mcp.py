"""Startup script for running the Cloudera Iceberg MCP Server as a CML Application.

CML builds Applications inside an isolated container per run, so there is no
need for a nested virtualenv/uv env: dependencies are installed straight into
the system Python. CML injects the externally-routable port via CDSW_APP_PORT;
we map that onto FastMCP's own FASTMCP_PORT/FASTMCP_HOST settings.

The app must bind 127.0.0.1, not 0.0.0.0: CML's own sidecar proxy already owns
0.0.0.0:<CDSW_APP_PORT> to terminate the public-facing connection, and forwards
internally to 127.0.0.1:<port> where our process is expected to listen. Binding
0.0.0.0 collides with that proxy ("address already in use").

This is a .py file (not a .sh script) because this workspace's Application
launcher runs script entrypoints through the ML Runtime's Python kernel rather
than through a shell (a bash script fails immediately with a SyntaxError), and
it uses PROJECT_DIR instead of __file__ because that same kernel-based
execution model runs the script's source as a notebook "cell" rather than as
a loaded file, so __file__ is never defined. CML always mounts a project's
root at /home/cdsw, so this path is reliable regardless of project name.
"""

import os
import subprocess
import sys

PROJECT_DIR = "/home/cdsw/iceberg-mcp-server"

port = os.environ.get("CDSW_APP_PORT")
if not port:
    sys.exit("CDSW_APP_PORT is not set - this script expects to run as a CML Application")

print("Installing iceberg-mcp-server dependencies into system Python...")
subprocess.run(
    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", PROJECT_DIR],
    check=True,
)

env = os.environ.copy()
env["MCP_TRANSPORT"] = env.get("MCP_TRANSPORT", "http")
env["FASTMCP_HOST"] = "127.0.0.1"
env["FASTMCP_PORT"] = port
env["PYTHONUNBUFFERED"] = "1"

print(f"Starting Iceberg MCP Server (transport={env['MCP_TRANSPORT']}) on {env['FASTMCP_HOST']}:{env['FASTMCP_PORT']}")
subprocess.run([sys.executable, "-m", "iceberg_mcp_server.server"], env=env, check=True)
