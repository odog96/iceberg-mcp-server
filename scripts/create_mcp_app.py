"""Create the Iceberg MCP Server application, and in CAI mode the chat app, through the Cloudera AI API.

The project template (.project-metadata.yaml) runs this as its last task. Run it yourself from a
session in the project when the template's environment variables were not picked up, or to add the
chat app later:

    python scripts/create_mcp_app.py --mode cai
    python scripts/create_mcp_app.py --mode test --test-user jdoe --details-file /home/cdsw/impala_vw_creds.txt

Modes (MCP_MODE, or --mode):
    cai    MCP server for apps hosted in this workspace, plus the chat app. Both run with CAI
           authentication ON. The chat app is trusted to act for its logged-in users
           (CAI_TRUSTED_CALLERS, default: you, because the chat app calls the server with your API key).
    entra  MCP server for an external app (e.g. a Foundry/Copilot agent) that sends Entra ID tokens.
           CAI authentication is OFF; the server checks every token (ENTRA_TENANT_ID, ENTRA_AUDIENCE).
    test   MCP server with NO identity check: every call runs as MCP_TEST_USER. CAI authentication
           is OFF, so anyone with the URL can query as that user. Stop it when not in use.

Settings come from the environment (project settings) or from a details file for the IMPALA_* ones
(same format as test_doas.py; existing environment wins). Each application gets a copy of the
settings it needs in its own environment, so it does not depend on project-level variables reaching
it; change them later in the application's settings. Anyone who can edit the project can read them.
An application whose subdomain is already used in this project is left alone, so re-running is safe.
Prints nothing secret.
"""

import argparse
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_doas import load_details_file  # noqa: E402

REPO_DIR = Path(__file__).resolve().parent.parent
RUNTIME_FILTER = '{"editor":"PBJ Workbench","kernel":"Python 3.12","edition":"Standard"}'
MODES = ("cai", "entra", "test")

# Each mode's identity settings. Another mode's settings must not be set, because applications can
# also see project-level variables and the server would then start in the wrong mode (or refuse to).
MODE_SETTINGS = {
    "cai": ("CAI_TRUSTED_CALLERS",),
    "entra": ("ENTRA_TENANT_ID", "ENTRA_AUDIENCE"),
    "test": ("MCP_TEST_USER",),
}
ENTRA_OPTIONAL = ("ENTRA_ISSUER", "ENTRA_JWKS_URI", "ENTRA_USER_CLAIMS", "ENTRA_REQUIRED_SCOPE", "USER_MAP")
CHAT_SETTINGS = (
    "LLM_PROVIDER",
    "CAI_INFERENCE_BASE_URL",
    "CAI_INFERENCE_MODEL",
    "CAI_INFERENCE_API_KEY",
    "BEDROCK_MODEL_ID",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "MAX_TOOL_CALLS",
)


def clean_host(host: str) -> str:
    return host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")


def plan_apps(mode: str, env: dict, script_dir: str, domain: str, subdomain: str) -> tuple[list[dict], list[str]]:
    """Return (applications to create, notes). Raises ValueError for a configuration that can't work."""
    if mode not in MODES:
        raise ValueError(f"MCP_MODE must be one of {', '.join(MODES)}, not {mode!r}")
    get = lambda k: (env.get(k) or "").strip()  # noqa: E731
    notes = []

    missing = [k for k in ("IMPALA_HOST", "IMPALA_USER") if not get(k)]
    if not (get("IMPALA_PASSWORD_B64") or get("IMPALA_PASSWORD")):
        missing.append("IMPALA_PASSWORD_B64")
    missing += [k for k in MODE_SETTINGS[mode] if not get(k)]
    if missing:
        raise ValueError(f"Missing settings for {mode} mode: {', '.join(missing)}")
    conflicting = [k for m, keys in MODE_SETTINGS.items() if m != mode for k in keys if get(k) and k != "MCP_TEST_USER"]
    if conflicting:
        raise ValueError(
            f"{', '.join(conflicting)} belong to another mode; clear them in the project settings for {mode} mode"
        )
    if mode != "test" and get("MCP_TEST_USER"):
        notes.append(f"MCP_TEST_USER is ignored in {mode} mode")

    mcp_env = {
        "IMPALA_HOST": clean_host(get("IMPALA_HOST")),
        "IMPALA_PORT": get("IMPALA_PORT") or "443",
        "IMPALA_USER": get("IMPALA_USER"),
        "IMPALA_PASSWORD_B64": get("IMPALA_PASSWORD_B64") or base64.b64encode(get("IMPALA_PASSWORD").encode()).decode(),
        "IMPALA_DATABASE": get("IMPALA_DATABASE") or "default",
        "MCP_TRANSPORT": "http",
    }
    for k in MODE_SETTINGS[mode] + (ENTRA_OPTIONAL if mode == "entra" else ()) + ("ALLOWED_USERS",):
        if get(k):
            mcp_env[k] = get(k)

    names = {
        "cai": "Iceberg MCP Server (CAI callers)",
        "entra": "Iceberg MCP Server (Entra ID tokens)",
        "test": "Iceberg MCP Server (test mode, no identity check)",
    }
    descriptions = {
        "cai": "MCP endpoint at /mcp for apps in this workspace. Needs CAI authentication ON.",
        "entra": "MCP endpoint at /mcp for external apps. Every call needs a valid Entra ID token.",
        "test": "MCP endpoint at /mcp. Anyone with the URL can run read-only queries as MCP_TEST_USER, "
        "so stop it when not in use.",
    }
    apps = [
        {
            "name": names[mode],
            "description": descriptions[mode],
            "subdomain": subdomain,
            "script": f"{script_dir}start_mcp.py",
            "environment": mcp_env,
            "bypass_authentication": mode != "cai",
        }
    ]

    if mode == "cai":
        if get("CAI_INFERENCE_BASE_URL") or get("BEDROCK_MODEL_ID"):
            chat_env = {k: get(k) for k in CHAT_SETTINGS if get(k)}
            chat_env["MCP_URL"] = f"https://{subdomain}.{domain}/mcp"
            apps.append(
                {
                    "name": "Iceberg data chat",
                    "description": "Chat with your Impala data. Queries run as the logged-in user.",
                    "subdomain": f"{subdomain}-chat",
                    "script": f"{script_dir}start_chat.py",
                    "environment": chat_env,
                    "bypass_authentication": False,
                }
            )
        else:
            notes.append(
                "Chat app not created: set CAI_INFERENCE_BASE_URL (Cloudera AI Inference) or BEDROCK_MODEL_ID "
                "(AWS Bedrock) in the project settings, then run this script again"
            )
    return apps, notes


def latest_runtime(client) -> str:
    runtimes = client.list_runtimes(search_filter=RUNTIME_FILTER, page_size=100).runtimes
    if not runtimes:
        sys.exit(f"No runtime matches {RUNTIME_FILTER}")
    return sorted(r.image_identifier for r in runtimes)[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=MODES, default=(os.getenv("MCP_MODE") or "").strip().lower() or None)
    ap.add_argument("--details-file", default=str(REPO_DIR / "impala_details.txt"))
    ap.add_argument("--test-user", help="test mode: Cloudera user every call runs as (or MCP_TEST_USER)")
    ap.add_argument("--database", help="overrides IMPALA_DATABASE (default: default)")
    ap.add_argument(
        "--subdomain",
        default=os.getenv("APP_SUBDOMAIN") or "iceberg-mcp",
        help="MCP server subdomain (or APP_SUBDOMAIN); the chat app gets <subdomain>-chat",
    )
    args = ap.parse_args()
    if not args.mode:
        ap.error(f"choose --mode ({', '.join(MODES)}) or set MCP_MODE")

    load_details_file(Path(args.details_file))
    if args.test_user:
        os.environ["MCP_TEST_USER"] = args.test_user
    if args.database:
        os.environ["IMPALA_DATABASE"] = args.database
    if args.mode == "cai" and not os.getenv("CAI_TRUSTED_CALLERS"):
        # The chat app calls the MCP server with the API key of whoever creates it: you.
        me = (os.getenv("HADOOP_USER_NAME") or "").strip().lower()
        if me:
            os.environ["CAI_TRUSTED_CALLERS"] = me

    # Application scripts are relative to the project root (/home/cdsw); the repo may be a subfolder.
    script_dir = "" if REPO_DIR == Path("/home/cdsw") else f"{REPO_DIR.name}/"
    try:
        apps, notes = plan_apps(args.mode, dict(os.environ), script_dir, os.environ["CDSW_DOMAIN"], args.subdomain)
    except ValueError as e:
        sys.exit(str(e))

    import cmlapi

    client = cmlapi.default_client()
    project_id = os.environ["CDSW_PROJECT_ID"]
    existing = {a.subdomain: a for a in client.list_applications(project_id, page_size=100).applications}
    runtime = latest_runtime(client)
    failed = False
    for app in apps:
        url = f"https://{app['subdomain']}.{os.environ['CDSW_DOMAIN']}"
        if app["subdomain"] in existing:
            print(f"exists, left alone: {existing[app['subdomain']].name} at {url}")
            continue
        req = cmlapi.CreateApplicationRequest(**app, kernel="python3", cpu=1, memory=2, runtime_identifier=runtime)
        try:
            created = client.create_application(req, project_id)
        except cmlapi.rest.ApiException as e:
            print(f"ERROR creating {app['name']}: {e.status}: {e.body[:600]}")
            failed = True
            continue
        login = "OFF (no CAI login)" if app["bypass_authentication"] else "ON"
        print(f"created {app['name']} ({created.status}) at {url}, CAI authentication {login}")
    print(f"MCP endpoint: https://{args.subdomain}.{os.environ['CDSW_DOMAIN']}/mcp")
    if args.mode == "cai":
        print(f"Trusted callers (may act for other users): {os.environ['CAI_TRUSTED_CALLERS']}")
    for note in notes:
        print(f"NOTE: {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
