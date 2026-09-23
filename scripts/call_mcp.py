"""B5: call a deployed (or local) MCP server with a bearer token.

    TOKEN=$(az account get-access-token --scope api://<app-id>/.default --query accessToken -o tsv)
    python scripts/call_mcp.py https://<app>.<domain>/mcp get_schema --token "$TOKEN"
    python scripts/call_mcp.py <url> execute_query --query "select 1" --token "$TOKEN"

Omit --token to test the unauthenticated / MCP_TEST_USER behaviour. Add --claims
to print the token's (unverified) claims so you can see which identity claim
the server will map, and whether iss/aud match your ENTRA_* settings.
"""

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx

from fastmcp import Client
from fastmcp.client.auth import BearerAuth


def decode_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("tool", choices=["get_schema", "execute_query", "whoami"])
    ap.add_argument("--query")
    ap.add_argument("--token", default=os.getenv("TOKEN"))
    ap.add_argument("--token-file", help="read the bearer token from this file (keeps it out of shell history)")
    ap.add_argument("--claims", action="store_true", help="print the token's unverified claims first")
    args = ap.parse_args()

    if args.token_file:
        args.token = Path(args.token_file).read_text().strip()

    if args.claims and args.token:
        claims = decode_claims(args.token)
        print(json.dumps({k: claims.get(k) for k in ("iss", "aud", "exp", "preferred_username", "upn", "email", "ver")}, indent=2))

    tool_args = {"query": args.query} if args.tool == "execute_query" else {}
    try:
        async with Client(args.url, auth=BearerAuth(args.token) if args.token else None) as c:
            res = await c.call_tool(args.tool, tool_args)
    except httpx.HTTPStatusError as e:
        print(f"HTTP {e.response.status_code}: rejected by the server (bad/missing/expired token, or wrong audience/issuer)")
        return 1
    print(res.content[0].text)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
