"""Guards the Cloudera AI project template (.project-metadata.yaml) so it cannot quietly rot.

The template is only exercised when someone imports the project into another workspace, which is
too late to find a typo. These checks cover what can be checked offline.
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
META = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
TASKS = META["tasks"]
ENV = META["environment_variables"]

# Required fields per task type, from Cloudera's AMP project specification.
REQUIRED_TASK_FIELDS = {
    "create_job": {"type", "name", "entity_label", "script", "kernel"},
    "run_session": {"type", "kernel", "cpu", "memory"},
    "start_application": {"type", "subdomain", "kernel", "script"},
}


def test_required_top_level_fields_and_limits():
    for key in ("name", "description", "author", "specification_version", "prototype_version"):
        assert META.get(key) not in (None, ""), key
    assert len(META["name"]) <= 200
    assert len(META["description"]) <= 2048
    assert len(META["author"]) <= 64
    assert len(str(META["specification_version"])) <= 16


def test_runtime_is_fully_specified():
    for rt in META["runtimes"]:
        assert {"editor", "kernel", "edition"} <= set(rt)


@pytest.mark.parametrize("task", TASKS, ids=lambda t: f"{t['type']}:{t.get('name', '')}")
def test_task_has_required_fields_and_real_script(task):
    assert task["type"] in REQUIRED_TASK_FIELDS, f"unexpected task type {task['type']}"
    assert REQUIRED_TASK_FIELDS[task["type"]] <= set(task)
    if "script" in task:
        assert (ROOT / task["script"]).is_file(), f"{task['script']} does not exist"


def test_applications_are_created_by_the_mode_aware_script():
    # The CAI login setting depends on MCP_MODE (ON for cai, OFF for entra/test), which a
    # start_application task cannot express, so the last task creates the applications instead.
    assert not [t for t in TASKS if t["type"] == "start_application"]
    assert TASKS[-1]["type"] == "run_session"
    assert TASKS[-1]["script"] == "scripts/create_mcp_app.py"


def test_every_environment_variable_is_one_the_code_reads():
    files = [ROOT / "src" / "iceberg_mcp_server" / f for f in ("server.py", "identity.py", "tools/impala_tools.py")]
    files += [ROOT / "scripts" / "create_mcp_app.py", ROOT / "chat_app" / "llm.py"]
    source = "".join(f.read_text() for f in files)
    for name in ENV:
        assert name in source, f"{name} is defined in the template but never read"


def test_password_has_no_default_and_is_required():
    assert ENV["IMPALA_PASSWORD_B64"]["default"] == ""
    assert ENV["IMPALA_PASSWORD_B64"]["required"] is True


def test_mode_defaults_to_cai_and_identity_settings_start_empty():
    # cai is the mode whose applications keep CAI login ON. Every identity setting starts empty,
    # so the deployer fills only the chosen mode's (create_mcp_app.py refuses another mode's).
    assert ENV["MCP_MODE"]["default"] == "cai"
    for name in ("CAI_TRUSTED_CALLERS", "ENTRA_TENANT_ID", "ENTRA_AUDIENCE", "ENTRA_ISSUER", "MCP_TEST_USER"):
        assert ENV[name]["default"] == "" and ENV[name]["required"] is False, name


def test_mode_values_match_the_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    from create_mcp_app import MODES

    for mode in MODES:
        assert mode in ENV["MCP_MODE"]["description"]


def test_optional_data_job_is_never_run_automatically():
    # It writes to the customer's warehouse, so it must be created but not run.
    assert not [t for t in TASKS if t["type"] == "run_job"]
