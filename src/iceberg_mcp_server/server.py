## Copyright (c) 2025 Cloudera, Inc. All Rights Reserved.
##
## This file is licensed under the Apache License Version 2.0 (the "License").
## You may not use this file except in compliance with the License.
## You may obtain a copy of the License at http:##www.apache.org/licenses/LICENSE-2.0.
##
## This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
## OF ANY KIND, either express or implied. Refer to the License for the specific
## permissions and limitations governing your use of the file.

import os
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from dotenv import load_dotenv
from starlette.responses import PlainTextResponse

load_dotenv()

from iceberg_mcp_server import identity
from iceberg_mcp_server.tools import impala_tools

# Entra bearer-token validation (signature, issuer, audience, expiry) happens in
# FastMCP before any tool runs. With no ENTRA_* config the server has no auth at
# all, which main() only permits in explicit MCP_TEST_USER test mode.
mcp = FastMCP(name="Cloudera Iceberg MCP Server via Impala", auth=identity.build_verifier())


# Plain routes so a browser and CML's health poll (CDSW_APP_POLLING_ENDPOINT=/) get a 200
# instead of a 404. Static text only: no auth, no user info, no Impala access.
@mcp.custom_route("/", methods=["GET"])
@mcp.custom_route("/health", methods=["GET"])
async def landing(request):
    return PlainTextResponse("Cloudera Iceberg MCP server is running. MCP endpoint: /mcp\n")


def _effective_user() -> str:
    """Cloudera user for this request. Raises (tool call fails) if it cannot be resolved."""
    try:
        return identity.resolve_effective_user(get_access_token())
    except identity.IdentityError as e:
        raise PermissionError(str(e)) from None


# Register functions as MCP tools
@mcp.tool()
def execute_query(query: str) -> str:
    """
    Execute a read-only SQL query on the Impala database and return results as JSON.
    The query runs as the authenticated end user.
    """
    return impala_tools.execute_query(query, _effective_user())


@mcp.tool()
def get_schema() -> str:
    """
    Retrieve the list of table names in the current Impala database.
    """
    return impala_tools.get_schema(_effective_user())


def main():
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if identity.auth_configured():
        if os.getenv("MCP_TEST_USER"):
            print("WARNING: MCP_TEST_USER is ignored because Entra auth is configured")
    elif os.getenv("MCP_TEST_USER"):
        print(f"WARNING: NO TOKEN VALIDATION. Every caller runs as fixed test user {os.environ['MCP_TEST_USER']!r}")
    else:
        raise SystemExit(
            "Refusing to start: set ENTRA_TENANT_ID and ENTRA_AUDIENCE (production) "
            "or MCP_TEST_USER (test deployments only)"
        )
    print(f"Starting Iceberg MCP Server via transport: {transport}")
    mcp.run(transport=transport)

if __name__ == "__main__":
    main()
