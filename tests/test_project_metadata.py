"""Guards the Cloudera AI project template (.project-metadata.yaml) so it cannot quietly rot.

The template is only exercised when someone imports the project into another workspace, which is
too late to find a typo. These checks cover what can be checked offline.
"""

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


def test_application_is_last_because_tasks_run_in_order():
    assert TASKS[-1]["type"] == "start_application"
    assert TASKS[-1]["script"] == "start_mcp.py"


def test_every_environment_variable_is_one_the_code_reads():
    source = "".join(
        (ROOT / "src" / "iceberg_mcp_server" / f).read_text()
        for f in ("server.py", "identity.py", "tools/impala_tools.py")
    )
    for name in ENV:
        assert name in source, f"{name} is defined in the template but never read by the server"


def test_password_has_no_default_and_is_required():
    assert ENV["IMPALA_PASSWORD_B64"]["default"] == ""
    assert ENV["IMPALA_PASSWORD_B64"]["required"] is True


def test_test_mode_template_needs_a_fixed_user_and_never_sets_entra():
    # Setting any ENTRA_* variable would switch the server to token checking, which contradicts
    # this template's purpose (and its unauthenticated application).
    assert ENV["MCP_TEST_USER"]["required"] is True and ENV["MCP_TEST_USER"]["default"] == ""
    assert not [k for k in ENV if k.startswith("ENTRA_")]
    assert TASKS[-1]["bypass_authentication"] is True


def test_optional_data_job_is_never_run_automatically():
    # It writes to the customer's warehouse, so it must be created but not run.
    assert not [t for t in TASKS if t["type"] == "run_job"]
