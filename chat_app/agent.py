"""One chat turn: the model answers, calling the MCP server's tools as the logged-in user.

    MCP_URL          the MCP server's endpoint, e.g. https://iceberg-mcp.<workspace-domain>/mcp
    MCP_API_KEY      CAI API key (v2) the chat app uses to call the MCP Application. Its owner must be
                     listed in the MCP server's CAI_TRUSTED_CALLERS. Default: CDSW_APIV2_KEY, the key of
                     the user this Application runs as.
    MAX_TOOL_CALLS   safety limit per turn (default 8)

The end user is sent as X-On-Behalf-Of; the MCP server accepts it only from a trusted caller.
"""

import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

SYSTEM_PROMPT = """You answer questions about data in an Apache Impala database, using the tools provided.
Call get_schema first: it returns every table with its columns. Use only those exact table and column names.
Queries are read-only and run as the user you are talking to, so some tables may be denied to them:
if a query is refused, say so plainly instead of guessing. Never invent data; every number in your
answer must come from a query result. Keep answers short and show the SQL you ran."""


def _mcp_client(user: str) -> Client:
    url = os.getenv("MCP_URL")
    key = os.getenv("MCP_API_KEY") or os.getenv("CDSW_APIV2_KEY")
    if not url or not key:
        raise RuntimeError("Set MCP_URL, and MCP_API_KEY unless CDSW_APIV2_KEY is available")
    headers = {"Authorization": f"Bearer {key}", "X-On-Behalf-Of": user}
    return Client(StreamableHttpTransport(url, headers=headers))


async def _run_turn(llm, user, history, on_tool_call):
    max_calls = int(os.getenv("MAX_TOOL_CALLS", "8"))
    async with _mcp_client(user) as mcp:
        tools = await mcp.list_tools()
        messages = llm.start(history)
        for _ in range(max_calls + 1):
            reply = llm.chat(SYSTEM_PROMPT, messages, tools)
            if not reply.tool_calls:
                return reply.text
            results = []
            for call in reply.tool_calls:
                res = await mcp.call_tool(call.name, call.arguments, raise_on_error=False)
                output = "\n".join(getattr(c, "text", "") for c in res.content) or "(no output)"
                on_tool_call(call.name, call.arguments, output)
                results.append((call, output))
            llm.add_tool_results(messages, results)
    return "Stopped: too many tool calls for one question. Try asking something narrower."


def run_turn(llm, user: str, history: list[dict], on_tool_call=lambda name, args, output: None) -> str:
    """history: [{"role": "user"|"assistant", "content": str}, ...] ending with the new question."""
    return asyncio.run(_run_turn(llm, user, history, on_tool_call))
