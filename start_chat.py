"""Startup script for running the Iceberg data chat app as a CML Application.

Same conventions as start_mcp.py (see its docstring): no __file__, bind 127.0.0.1:$CDSW_APP_PORT.
The Application must run with CAI authentication ON, so the app learns who is logged in. Settings
are described in chat_app/llm.py (model) and chat_app/agent.py (MCP server).
"""

import glob
import os
import subprocess
import sys


# CML always mounts a project's root at /home/cdsw. The repo is either that root (imported as a
# project) or a folder inside it (cloned into an existing project), so look in both places.
# MCP_PROJECT_DIR overrides the search.
def find_repo_dir():
    if os.getenv("MCP_PROJECT_DIR"):
        return os.environ["MCP_PROJECT_DIR"]
    for d in ["/home/cdsw", *sorted(glob.glob("/home/cdsw/*/"))]:
        pyproject = os.path.join(d, "pyproject.toml")
        if os.path.isfile(pyproject) and 'name = "iceberg-mcp-server"' in open(pyproject).read():
            return d.rstrip("/")
    sys.exit("Cannot find the iceberg-mcp-server repo under /home/cdsw; set MCP_PROJECT_DIR")


PROJECT_DIR = find_repo_dir()

port = os.environ.get("CDSW_APP_PORT")
if not port:
    sys.exit("CDSW_APP_PORT is not set - this script expects to run as a CML Application")

print("Installing chat app dependencies into system Python...")
subprocess.run(
    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", f"{PROJECT_DIR}[chat]"],
    check=True,
)

print(f"Starting chat app on 127.0.0.1:{port}")
subprocess.run(
    [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        os.path.join(PROJECT_DIR, "chat_app", "app.py"),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        port,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ],
    check=True,
)
