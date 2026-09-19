# Cloudera Iceberg MCP Server (via Impala)

This is a A Model Context Protocol server that provides read-only access to Iceberg tables via Apache Impala. This server enables LLMs to inspect database schemas and execute read-only queries.

- `execute_query(query: str)`: Run any SQL query on Impala and return the results as JSON.
- `get_schema()`: List all tables available in the current database.

## Deployment

This fork is deployed as a Cloudera Machine Learning (CML) Application using `start_mcp.py`, which installs the package with `pip` and serves MCP over HTTP. See `docs/build-plan.md` for the architecture, setup runbook and build steps.

## Usage with AI frameworks

The `./examples` folder contains several examples how to integrate this MCP Server with common AI Frameworks like LangChain/LangGraph, OpenAI SDK.

### Transport

The MCP server's transport protocol is configurable via the `MCP_TRANSPORT` environment variable. Supported values:
- `stdio` **(default)** — communicate over standard input/output. Useful for local tools, command-line scripts, and integrations with clients like Claude Desktop.
- `http` - expose an HTTP server. Useful for web-based deployments, microservices, exposing MCP over a network.
- `sse` — use Server-Sent Events (SSE) transport. Useful for existing web-based deployments that rely on SSE.


*Copyright (c) 2025 - Cloudera, Inc. All rights reserved.*
