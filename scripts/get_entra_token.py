"""Sign in with a real Entra account (device-code flow) and save an access token for the MCP server.

    python scripts/get_entra_token.py

You get a URL and a code: open the URL in a browser, enter the code, and sign in as the user
whose token you want. The token is written to .entra_token (gitignored, mode 600) and only a
summary of its claims is printed, so the token itself never lands in your terminal history.

    python scripts/call_mcp.py https://<app>/mcp get_schema --token-file .entra_token --claims

Settings default to this POC's tenant and apps; override with flags or env vars
ENTRA_TENANT_ID, ENTRA_CLIENT_APP_ID, ENTRA_SERVER_APP_ID.
Requires the client app to have "Allow public client flows" = Yes and admin consent granted.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import msal

TENANT = os.getenv("ENTRA_TENANT_ID", "650a1000-e5e3-40bf-97a9-d62002a0934b")
CLIENT = os.getenv("ENTRA_CLIENT_APP_ID", "ac7b4ab5-79c3-4962-9c9a-3131a90f2217")
SERVER = os.getenv("ENTRA_SERVER_APP_ID", "1217683d-abaf-41ff-bbf7-c53a0a8814a9")


def decode_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--client-id", default=CLIENT, help="the iceberg-mcp-client app")
    ap.add_argument("--server-id", default=SERVER, help="the iceberg-mcp-server app (token audience)")
    ap.add_argument("--out", default=".entra_token")
    args = ap.parse_args()

    scope = f"api://{args.server_id}/access_as_user"
    app = msal.PublicClientApplication(args.client_id, authority=f"https://login.microsoftonline.com/{args.tenant}")
    flow = app.initiate_device_flow(scopes=[scope])
    if "user_code" not in flow:
        print("Could not start device flow:", flow.get("error"), flow.get("error_description"))
        return 1
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)  # blocks until you finish signing in

    if "access_token" not in result:
        print("FAILED:", result.get("error"))
        print(result.get("error_description"))
        return 1

    out = Path(args.out)
    out.write_text(result["access_token"])
    out.chmod(0o600)
    claims = decode_claims(result["access_token"])
    print(f"\nSaved token to {out} (valid until exp={claims.get('exp')}).")
    print("Claims the server will see:")
    for k in ("iss", "aud", "ver", "scp", "preferred_username", "upn", "email", "name", "oid", "tid"):
        print(f"  {k}: {claims.get(k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
