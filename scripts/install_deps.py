"""Install this project's dependencies into the session's Python.

Run by the Cloudera AI project template (.project-metadata.yaml) before anything else, so the
optional sample-data job and any session scripts work straight away. start_mcp.py performs the
same install for the application, so this is safe to run more than once.
"""

import subprocess
import sys

# CML always mounts a project's root at /home/cdsw.
PROJECT_DIR = "/home/cdsw"

subprocess.run(
    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", PROJECT_DIR],
    check=True,
)
print("Dependencies installed.")
