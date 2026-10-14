"""Create the Iceberg MCP Server application (test mode) through the Cloudera AI API.

Does what the start_application task in .project-metadata.yaml does, for when the template's
environment variables are not picked up. Run it from a session in the target project:

    python scripts/create_mcp_app.py --details-file /home/cdsw/impala_vw_creds.txt --test-user ozarate

Connection settings come from the environment (IMPALA_HOST, IMPALA_PORT, IMPALA_USER,
IMPALA_PASSWORD_B64 or IMPALA_PASSWORD, IMPALA_DATABASE) or from a details file (same format as
test_doas.py; existing environment wins). The password is stored base64-encoded in the
application's environment, so anyone who can edit the project can read it. Prints nothing secret.
"""

import argparse
import base64
import os
import sys
from pathlib import Path

import cmlapi

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_doas import load_details_file  # noqa: E402

REPO_DIR = Path(__file__).resolve().parent.parent
RUNTIME_FILTER = '{"editor":"PBJ Workbench","kernel":"Python 3.12","edition":"Standard"}'


def latest_runtime(client) -> str:
    runtimes = client.list_runtimes(search_filter=RUNTIME_FILTER, page_size=100).runtimes
    if not runtimes:
        sys.exit(f"No runtime matches {RUNTIME_FILTER}")
    return sorted(r.image_identifier for r in runtimes)[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--details-file", default=str(REPO_DIR / "impala_details.txt"))
    ap.add_argument("--test-user", default=os.getenv("MCP_TEST_USER"), help="Cloudera user every call runs as")
    ap.add_argument("--database", help="overrides IMPALA_DATABASE (default: default)")
    ap.add_argument("--subdomain", default="iceberg-mcp")
    args = ap.parse_args()
    if not args.test_user:
        ap.error("--test-user is required (or set MCP_TEST_USER)")

    load_details_file(Path(args.details_file))
    if args.database:
        os.environ["IMPALA_DATABASE"] = args.database
    missing = [k for k in ("IMPALA_HOST", "IMPALA_USER") if not os.getenv(k)]
    if not (os.getenv("IMPALA_PASSWORD_B64") or os.getenv("IMPALA_PASSWORD")):
        missing.append("IMPALA_PASSWORD_B64 or IMPALA_PASSWORD")
    if missing:
        sys.exit(f"Missing settings: {', '.join(missing)}")

    password_b64 = os.getenv("IMPALA_PASSWORD_B64") or base64.b64encode(os.environ["IMPALA_PASSWORD"].encode()).decode()
    env = {
        "IMPALA_HOST": os.environ["IMPALA_HOST"].removeprefix("https://").rstrip("/"),
        "IMPALA_PORT": os.getenv("IMPALA_PORT", "443"),
        "IMPALA_USER": os.environ["IMPALA_USER"],
        "IMPALA_PASSWORD_B64": password_b64,
        "IMPALA_DATABASE": os.getenv("IMPALA_DATABASE", "default"),
        "MCP_TEST_USER": args.test_user,
        "MCP_TRANSPORT": "http",
    }

    # Application scripts are relative to the project root (/home/cdsw); the repo may be a subfolder.
    script = "start_mcp.py" if REPO_DIR == Path("/home/cdsw") else f"{REPO_DIR.name}/start_mcp.py"

    client = cmlapi.default_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    existing = [
        a for a in client.list_applications(project_id, page_size=100).applications if a.subdomain == args.subdomain
    ]
    if existing:
        sys.exit(f"An application already uses subdomain {args.subdomain!r} ({existing[0].name}, {existing[0].status})")

    req = cmlapi.CreateApplicationRequest(
        name="Iceberg MCP Server (test mode, no token check)",
        description="MCP endpoint at /mcp. Anyone with the URL can run read-only queries as MCP_TEST_USER, "
        "so stop it when not in use.",
        subdomain=args.subdomain,
        script=script,
        kernel="python3",
        cpu=1,
        memory=2,
        environment=env,
        bypass_authentication=True,
        runtime_identifier=latest_runtime(client),
    )
    try:
        app = client.create_application(req, project_id)
    except cmlapi.rest.ApiException as e:
        print(f"ERROR {e.status}: {e.body[:600]}")
        return 1
    print(f"created {app.id} ({app.status}), script {script}, database {env['IMPALA_DATABASE']}")
    print(f"endpoint: https://{args.subdomain}.{os.environ['CDSW_DOMAIN']}/mcp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
