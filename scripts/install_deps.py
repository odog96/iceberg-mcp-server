"""Install this project's dependencies into the session's Python.

Run by the Cloudera AI project template (.project-metadata.yaml) before anything else, so the
optional sample-data job and any session scripts work straight away. start_mcp.py performs the
same install for the application, so this is safe to run more than once.
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

subprocess.run(
    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", PROJECT_DIR],
    check=True,
)
print("Dependencies installed.")
